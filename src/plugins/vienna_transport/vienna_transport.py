import requests
import json
import logging
import time
import socket
import gc
import os
import resource
from datetime import datetime
from urllib3.exceptions import ProtocolError
from requests.exceptions import ConnectionError, Timeout, RequestException
from contextlib import contextmanager
from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image, ImageDraw, ImageFont
from utils.app_utils import get_font

logger = logging.getLogger(__name__)

class ViennaTransport(BasePlugin):
    """Plugin for displaying Vienna public transport departure times."""

    def __init__(self, config, **dependencies):
        super().__init__(config, **dependencies)
        self.api_base_url = "https://www.wienerlinien.at/ogd_realtime/monitor"
        self.request_counter = 0
        self.max_requests_before_cleanup = 100  # Force cleanup every 100 requests

    @contextmanager
    def _get_session(self):
        """Context manager for session that ensures proper cleanup."""
        session = None
        try:
            session = requests.Session()
            # Minimal adapter to reduce resource usage
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=1,
                pool_maxsize=1,
                max_retries=0,
                pool_block=False
            )
            session.mount('https://', adapter)
            session.mount('http://', adapter)
            # Force connection close to prevent lingering connections
            session.headers.update({'Connection': 'close'})
            yield session
        finally:
            if session:
                try:
                    # Close all adapters and clear pools
                    for adapter in session.adapters.values():
                        adapter.close()
                    session.close()
                except Exception as e:
                    logger.debug(f"Error closing session: {e}")
            # Force garbage collection to free file descriptors
            gc.collect()
    
    def generate_image(self, settings, device_config):
        """Generate an image showing departure times for Vienna public transport."""
        try:
            # Get display dimensions
            dimensions = device_config.get_resolution()
            if device_config.get_config("orientation") == "vertical":
                dimensions = dimensions[::-1]

            # Simplified stops configuration - names and directions will be fetched from API
            stops_config = {
                'Barichgasse': {
                    'rbl_numbers': ['266', '281'],  # Stubentor and St. Marx
                    'lines': '74A'  # Optional filter for specific lines
                },
                'Rochusgasse': {
                    'rbl_numbers': ['4903', '4914'],  # Ottakring and Simmering
                    'lines': 'U3'  # Optional filter for specific lines
                },
                'Eslarngasse': {
                    'rbl_numbers': ['2502'],  # Lusthaus
                    'lines': '77A'  # Optional filter for specific lines
                },
                'Hintzerstraße': {
                    'rbl_numbers': ['254', '267'],  # Karlsplatz and Wittelsbachstraße
                    'lines': '4A'  # Optional filter for specific lines
                },
            }

            # Fetch departure data for all stops
            departure_data = self._fetch_departure_data(stops_config)

            # Create image using PIL instead of HTML rendering
            image = self._draw_transport_layout(dimensions, departure_data)
            if not image:
                raise RuntimeError("Failed to draw transport layout")

            return image

        except Exception as e:
            logger.error(f"Error generating Vienna transport image: {e}")
            raise RuntimeError(f"Error: {str(e)}")
    
    def _fetch_departure_data(self, stops_config):
        """Fetch departure data for all configured stops using a single API request."""
        # Collect all RBL numbers and their corresponding stop configurations
        all_rbl_numbers = []
        rbl_to_stop_mapping = {}  # Maps RBL number to stop configuration

        for stop_id, stop_info in stops_config.items():
            rbl_numbers = stop_info.get('rbl_numbers', [])
            monitored_lines = stop_info.get('lines', '').strip()

            if not rbl_numbers:
                logger.warning(f"No RBL numbers configured for stop group: {stop_id}")
                continue

            # Parse monitored lines
            line_filter = []
            if monitored_lines:
                line_filter = [line.strip().upper() for line in monitored_lines.split(',')]

            for rbl_number in rbl_numbers:
                rbl_number = rbl_number.strip()
                if rbl_number:
                    all_rbl_numbers.append(rbl_number)
                    rbl_to_stop_mapping[rbl_number] = {
                        'stop_id': stop_id,
                        'line_filter': line_filter
                    }

        if not all_rbl_numbers:
            logger.warning("No valid RBL numbers found in configuration")
            return []

        # Make single API request for all RBL numbers
        rbl_params = ','.join(all_rbl_numbers)
        url = f"{self.api_base_url}?rbl={rbl_params}&sender=vienna_transport_plugin"

        # Try fetching with retry logic and fresh sessions
        data = self._fetch_with_retry(url)
        if data:
            # Process the combined response
            return self._process_combined_response(data, rbl_to_stop_mapping, stops_config)
        else:
            logger.error("Failed to fetch departure data after all retries")
            return []

    def _fetch_with_retry(self, url, max_retries=3, initial_delay=1):
        """Fetch data with retry logic and proper resource cleanup."""
        last_exception = None

        # Increment request counter and force cleanup periodically
        self.request_counter += 1
        if self.request_counter >= self.max_requests_before_cleanup:
            logger.info(f"Forcing cleanup after {self.request_counter} requests")
            self.request_counter = 0
            gc.collect()
            time.sleep(1)  # Give system time to cleanup

        # Check if we're approaching file descriptor limits
        try:
            soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            # Count open file descriptors (Linux-specific)
            if os.path.exists('/proc/self/fd'):
                open_fds = len(os.listdir('/proc/self/fd'))
                if open_fds > soft * 0.8:  # Warning at 80% usage
                    logger.warning(f"High file descriptor usage: {open_fds}/{soft}")
                    # Force aggressive cleanup
                    gc.collect()
                    time.sleep(0.5)  # Give system time to release resources
        except:
            pass  # Not critical if we can't check

        for attempt in range(max_retries):
            try:
                # Use context manager to ensure proper cleanup
                with self._get_session() as session:
                    logger.info(f"Attempt {attempt + 1}/{max_retries}: Fetching Vienna transport data")

                    # Make request with explicit timeout and stream=False
                    response = session.get(url, timeout=10, stream=False)
                    response.raise_for_status()

                    # Parse JSON and immediately close response
                    data = response.json()
                    response.close()

                    logger.info(f"Successfully fetched data on attempt {attempt + 1}")
                    return data

            except (ConnectionError, ProtocolError, OSError) as e:
                last_exception = e
                error_str = str(e)
                if "Too many open files" in error_str or "errno 24" in error_str:
                    logger.error(f"File descriptor exhaustion detected: {e}")
                    # Emergency cleanup
                    gc.collect()
                    time.sleep(2)  # Give OS time to reclaim resources
                elif "Invalid argument" in error_str or "errno 22" in error_str:
                    logger.warning(f"Connection error (possibly FD exhaustion) on attempt {attempt + 1}: {e}")
                    gc.collect()
                else:
                    logger.warning(f"Connection error on attempt {attempt + 1}: {e}")

            except Timeout as e:
                last_exception = e
                logger.warning(f"Timeout on attempt {attempt + 1}: {e}")

            except RequestException as e:
                last_exception = e
                logger.warning(f"Request error on attempt {attempt + 1}: {e}")

            except Exception as e:
                last_exception = e
                logger.error(f"Unexpected error on attempt {attempt + 1}: {e}")

            # If not the last attempt, wait before retrying
            if attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt)
                logger.info(f"Waiting {delay} seconds before retry...")
                time.sleep(delay)
                # Extra cleanup between retries
                gc.collect()

        # All retries failed
        logger.error(f"All {max_retries} attempts failed. Last error: {last_exception}")
        return None

    def _process_combined_response(self, data, rbl_to_stop_mapping, stops_config):
        """Process the combined API response and group monitors by stop."""
        # Initialize stop data structure
        stop_data = {}

        try:
            if 'data' in data and 'monitors' in data['data']:
                monitors = data['data']['monitors']

                # Group monitors by stop based on RBL number
                for monitor in monitors:
                    rbl_number = str(monitor.get('locationStop', {}).get('properties', {}).get('attributes', {}).get('rbl', ''))

                    if rbl_number not in rbl_to_stop_mapping:
                        continue

                    stop_mapping = rbl_to_stop_mapping[rbl_number]
                    stop_id = stop_mapping['stop_id']
                    line_filter = stop_mapping['line_filter']

                    # Initialize stop data if not exists
                    if stop_id not in stop_data:
                        stop_data[stop_id] = {
                            'name': None,
                            'lines': {}
                        }

                    # Set stop name from the monitor
                    if stop_data[stop_id]['name'] is None and 'locationStop' in monitor:
                        stop_data[stop_id]['name'] = monitor['locationStop'].get('properties', {}).get('title', 'Unknown Stop')

                    # Process lines for this monitor
                    if 'lines' in monitor:
                        for line_info in monitor['lines']:
                            line_name = line_info.get('name', '').strip()

                            # Filter lines if specified
                            if line_filter and line_name.upper() not in line_filter:
                                continue

                            if line_name not in stop_data[stop_id]['lines']:
                                stop_data[stop_id]['lines'][line_name] = {}

                            # Process departures - collect first 2 departures with their directions
                            if 'departures' in line_info and 'departure' in line_info['departures']:
                                departures = line_info['departures']['departure']
                                if not isinstance(departures, list):
                                    departures = [departures]

                                # Get the first 2 departures to determine directions
                                first_two_departures = departures[:2]

                                # Collect unique directions from first 2 departures
                                directions_in_first_two = []
                                departure_data_by_direction = {}

                                for departure in first_two_departures:
                                    # Try to get direction from vehicle first, then from line itself
                                    direction = departure.get('vehicle', {}).get('towards', '')
                                    if not direction:
                                        direction = line_info.get('towards', '')
                                    direction = self._sanitize_towards(direction)
                                    countdown = departure.get('departureTime', {}).get('countdown', None)

                                    if direction not in directions_in_first_two:
                                        directions_in_first_two.append(direction)
                                        departure_data_by_direction[direction] = []

                                    # Add countdown time
                                    if countdown is not None:
                                        if countdown == 0:
                                            time_display = "*"
                                        else:
                                            time_display = str(countdown)
                                        departure_data_by_direction[direction].append(time_display)

                                # Store as single entry with directions array and times
                                if directions_in_first_two:
                                    # Use RBL number as key to ensure one entry per RBL
                                    rbl_key = f"rbl_{rbl_number}"
                                    if rbl_key not in stop_data[stop_id]['lines'][line_name]:
                                        stop_data[stop_id]['lines'][line_name][rbl_key] = {
                                            'directions': directions_in_first_two,
                                            'times_by_direction': departure_data_by_direction
                                        }

        except Exception as e:
            logger.error(f"Error processing combined API response: {e}")

        # Convert to departure_data format and sort/limit departures
        # Preserve the order from stops_config by iterating in that order
        departure_data = []
        for stop_id in stops_config.keys():
            if stop_id in stop_data:
                data_for_stop = stop_data[stop_id]
                if data_for_stop['lines'] and data_for_stop['name']:
                    # Sort RBL entries within each line according to the order in stops_config
                    stop_rbl_numbers = stops_config[stop_id].get('rbl_numbers', [])

                    # Sort and limit departures per direction to 2
                    for line_name in data_for_stop['lines']:
                        # Create ordered dict to maintain RBL order from config
                        ordered_rbl_data = {}

                        # First, process RBLs in the order they appear in config
                        for rbl_number in stop_rbl_numbers:
                            rbl_key = f"rbl_{rbl_number.strip()}"
                            if rbl_key in data_for_stop['lines'][line_name]:
                                rbl_data = data_for_stop['lines'][line_name][rbl_key]

                                def sort_key(time_str):
                                    if time_str == "*":
                                        return 0
                                    try:
                                        return int(time_str)
                                    except:
                                        return 999

                                # Sort times for each direction
                                for direction in rbl_data['times_by_direction']:
                                    rbl_data['times_by_direction'][direction].sort(key=sort_key)
                                    rbl_data['times_by_direction'][direction] = rbl_data['times_by_direction'][direction][:2]

                                ordered_rbl_data[rbl_key] = rbl_data

                        # Replace the unordered dict with ordered one
                        data_for_stop['lines'][line_name] = ordered_rbl_data

                    departure_data.append(data_for_stop)


        # Debug logging instead of pprint to avoid potential file operations
        logger.debug(f"Processed {len(departure_data)} stops with departure data")

        return departure_data

    def _draw_rounded_rectangle(self, draw, bounds, radius, fill=None, outline=None):
        """Draw a rounded rectangle using PIL primitives."""
        x1, y1, x2, y2 = bounds

        # Draw the main rectangle (without corners)
        draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill, outline=outline)
        draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill, outline=outline)

        # Draw the four rounded corners
        draw.pieslice([x1, y1, x1 + 2*radius, y1 + 2*radius], 180, 270, fill=fill, outline=outline)
        draw.pieslice([x2 - 2*radius, y1, x2, y1 + 2*radius], 270, 360, fill=fill, outline=outline)
        draw.pieslice([x1, y2 - 2*radius, x1 + 2*radius, y2], 90, 180, fill=fill, outline=outline)
        draw.pieslice([x2 - 2*radius, y2 - 2*radius, x2, y2], 0, 90, fill=fill, outline=outline)

    def _sanitize_towards(self, towards_text):
        """Sanitize towards text by trimming whitespace and capitalizing properly."""
        if not towards_text:
            return "Unknown Direction"

        # Trim whitespace
        sanitized = towards_text.strip()

        if not sanitized:
            return "Unknown Direction"

        # Capitalize: first letter of every word uppercase
        return sanitized.title()

    def _draw_transport_layout(self, dimensions, departure_data):
        """Draw the transport layout manually using PIL."""
        width, height = dimensions

        # Create image with white background
        image = Image.new('RGB', (width, height), 'white')
        draw = ImageDraw.Draw(image)

        # Layout constants
        side_padding = 10  # Only left and right padding
        line_square_size = 80  # Size of the line number squares
        line_name_font_size = 34  # Font size for line name text inside squares
        gap_after_square = 15
        # Calculate row height accounting for border gaps between rows
        border_gaps = 3  # 3 gaps between 4 rows
        row_height = (height - border_gaps) / 4  # Height allocated for each line row
        row_content_offset = 10  # Vertical offset to move content up within each row
        direction_line_height = 45  # Height between direction lines
        direction_font_size = 32  # Font size for direction text

        # Define font sizes and load fonts
        try:
            line_name_font = get_font("Jost", line_name_font_size, "bold")  # Bold font for line name
            direction_font = get_font("Jost", direction_font_size, "normal")  # Larger font for directions
            time_font = get_font("Jost", direction_font_size, "bold")  # Bold font for times
            time_normal_font = get_font("Jost", direction_font_size, "normal")  # Normal font for "min" and pipe

            # Fallback to default fonts if get_font returns None
            if line_name_font is None:
                line_name_font = ImageFont.load_default()
            if direction_font is None:
                direction_font = ImageFont.load_default()
            if time_font is None:
                time_font = ImageFont.load_default()
            if time_normal_font is None:
                time_normal_font = ImageFont.load_default()
        except Exception as e:
            logger.error(f"Error loading fonts: {e}")
            line_name_font = ImageFont.load_default()
            direction_font = ImageFont.load_default()
            time_font = ImageFont.load_default()
            time_normal_font = ImageFont.load_default()

        # Colors
        line_square_bg = '#000000'  # Black for line squares
        text_color = '#000000'      # Black text
        border_color = '#CCCCCC'    # Light gray borders

        current_y = 0

        # Draw each stop's data
        for stop in departure_data:
            if not stop.get('lines'):
                continue

            # Draw each line for this stop
            for line_name, rbl_data_dict in stop['lines'].items():
                if current_y + row_height > height:
                    break  # Not enough space for more rows

                # Draw line square on the left
                square_x = side_padding
                square_y = current_y + (row_height - line_square_size) // 2

                # Draw rounded square background
                self._draw_rounded_rectangle(draw,
                                           [square_x, square_y, square_x + line_square_size, square_y + line_square_size],
                                           radius=8, fill=line_square_bg)

                # Draw line name in center of square
                text_bbox = draw.textbbox((0, 0), line_name, font=line_name_font)
                text_width = text_bbox[2] - text_bbox[0]
                text_height = text_bbox[3] - text_bbox[1]
                text_x = square_x + (line_square_size - text_width) // 2
                text_y = square_y + (line_square_size - text_height) // 2 - text_bbox[1]
                draw.text((text_x, text_y), line_name, fill='white', font=line_name_font)

                # Draw directions area starting after the gap
                directions_x = square_x + line_square_size + gap_after_square
                directions_y = current_y - row_content_offset

                # Process each RBL entry for this line
                direction_y_offset = 0
                for rbl_key, rbl_data in rbl_data_dict.items():
                    if direction_y_offset + direction_line_height > row_height:
                        break  # Not enough space in this row

                    direction_row_y = directions_y + direction_y_offset + direction_line_height // 2

                    # Handle multiple directions with truncation and two-column layout
                    directions = rbl_data['directions']
                    if len(directions) > 1:
                        # Calculate available width for direction text (leave space for times on the right)
                        times_area_width = 220  # Increased width needed for times display with consistent spacing
                        right_margin = 10  # Reduced margin to the right
                        max_direction_width = width - directions_x - times_area_width - right_margin
                        truncated_directions = []

                        for direction in directions:
                            truncated_directions.append(direction)

                        # For two-column layout, each direction gets roughly half the width minus pipe separator
                        if len(truncated_directions) >= 2:
                            pipe_separator = " | "
                            pipe_width = draw.textbbox((0, 0), pipe_separator, font=direction_font)[2] - draw.textbbox((0, 0), pipe_separator, font=direction_font)[0]
                            available_per_direction = (max_direction_width - pipe_width) // 2

                            # Truncate each direction to fit its column
                            final_directions = []
                            for direction in truncated_directions[:2]:  # Only take first 2 directions
                                text_bbox = draw.textbbox((0, 0), direction, font=direction_font)
                                text_width = text_bbox[2] - text_bbox[0]

                                if text_width > available_per_direction:
                                    # Truncate with ellipsis
                                    truncated = direction
                                    while len(truncated) > 3:
                                        test_text = truncated[:-3] + "..."
                                        test_bbox = draw.textbbox((0, 0), test_text, font=direction_font)
                                        if test_bbox[2] - test_bbox[0] <= available_per_direction:
                                            truncated = test_text
                                            break
                                        truncated = truncated[:-1]
                                    final_directions.append(truncated)
                                else:
                                    final_directions.append(direction)

                            combined_direction = f"{final_directions[0]} | {final_directions[1] if len(final_directions) > 1 else ''}"
                        else:
                            # Single direction, truncate to full width
                            direction = truncated_directions[0]
                            text_bbox = draw.textbbox((0, 0), direction, font=direction_font)
                            text_width = text_bbox[2] - text_bbox[0]

                            if text_width > max_direction_width:
                                truncated = direction
                                while len(truncated) > 3:
                                    test_text = truncated[:-3] + "..."
                                    test_bbox = draw.textbbox((0, 0), test_text, font=direction_font)
                                    if test_bbox[2] - test_bbox[0] <= max_direction_width:
                                        truncated = test_text
                                        break
                                    truncated = truncated[:-1]
                                combined_direction = truncated
                            else:
                                combined_direction = direction
                    else:
                        # Single direction, use as is
                        combined_direction = directions[0] if directions else ""

                    # Draw combined direction name
                    draw.text((directions_x, direction_row_y), combined_direction, fill=text_color, font=direction_font)

                    # Collect all times from all directions for this RBL
                    all_times = []
                    for direction in rbl_data['directions']:
                        if direction in rbl_data['times_by_direction']:
                            all_times.extend(rbl_data['times_by_direction'][direction])

                    # Sort and limit to 2 times
                    def sort_key(time_str):
                        if time_str == "*":
                            return 0
                        try:
                            return int(time_str)
                        except:
                            return 999

                    all_times.sort(key=sort_key)
                    times_to_show = all_times[:2]

                    # Draw departure times (right-aligned with consistent spacing)
                    if times_to_show and len(times_to_show) > 0:
                        # Create fixed-width boxes for each time to ensure consistent alignment
                        time_box_width = 90  # Fixed width for each time box
                        separator_width = 70  # Fixed width for separator

                        # Calculate total width needed
                        if len(times_to_show) == 1:
                            total_times_width = time_box_width
                        else:
                            total_times_width = (time_box_width * len(times_to_show)) + (separator_width * (len(times_to_show) - 1))

                        # Position times from right edge
                        right_aligned_x = width - side_padding - total_times_width

                        # Calculate explicit positions for each time box
                        box_positions = []
                        for i in range(len(times_to_show)):
                            box_start_x = right_aligned_x + i * (time_box_width + separator_width)
                            box_positions.append(box_start_x)

                        # Draw each time in its 80px box
                        for i, time in enumerate(times_to_show):
                            box_start_x = box_positions[i]

                            if time == "*":
                                # Right-align asterisk in the 80px box to match single digit positioning
                                text_bbox = draw.textbbox((0, 0), "*", font=time_font)
                                text_width = text_bbox[2] - text_bbox[0]
                                text_x = box_start_x + time_box_width - text_width
                                draw.text((text_x, direction_row_y), "*", fill=text_color, font=time_font)
                            else:
                                if i == len(times_to_show) - 1:  # Last time gets 'min'
                                    # Right-align "X min" in the 80px box
                                    # Calculate width of full text to position it right-aligned
                                    number_bbox = draw.textbbox((0, 0), str(time), font=time_font)
                                    min_bbox = draw.textbbox((0, 0), " min", font=time_normal_font)
                                    total_width = (number_bbox[2] - number_bbox[0]) + (min_bbox[2] - min_bbox[0])

                                    # Position right-aligned in box
                                    text_start_x = box_start_x + time_box_width - total_width

                                    # Draw number with bold font
                                    draw.text((text_start_x, direction_row_y), str(time), fill=text_color, font=time_font)

                                    # Draw " min" with normal font
                                    min_x = text_start_x + (number_bbox[2] - number_bbox[0])
                                    draw.text((min_x, direction_row_y), " min", fill=text_color, font=time_normal_font)
                                else:
                                    # Right-align just the number in the 80px box
                                    text_bbox = draw.textbbox((0, 0), str(time), font=time_font)
                                    text_width = text_bbox[2] - text_bbox[0]
                                    text_x = box_start_x + time_box_width - text_width
                                    draw.text((text_x, direction_row_y), str(time), fill=text_color, font=time_font)

                        # Draw separators between boxes
                        for i in range(len(times_to_show) - 1):
                            # Pipe centered between box i and box i+1
                            separator_center_x = box_positions[i] + time_box_width + separator_width // 2
                            # Get pipe width and center it
                            pipe_bbox = draw.textbbox((0, 0), "|", font=time_normal_font)
                            pipe_width = pipe_bbox[2] - pipe_bbox[0]
                            pipe_x = separator_center_x - pipe_width // 2
                            draw.text((pipe_x, direction_row_y), "|", fill=text_color, font=time_normal_font)

                    direction_y_offset += direction_line_height

                # Draw horizontal border after this line row
                current_y += row_height
                if current_y < height:
                    draw.line([side_padding, current_y, width - side_padding, current_y],
                            fill=border_color, width=1)
                current_y += 1  # Small gap after border

        return image
    
