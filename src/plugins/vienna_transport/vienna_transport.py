import requests
import json
import logging
from datetime import datetime
from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image, ImageDraw, ImageFont
from utils.app_utils import get_font

logger = logging.getLogger(__name__)

class ViennaTransport(BasePlugin):
    """Plugin for displaying Vienna public transport departure times."""
    
    def __init__(self, config, **dependencies):
        super().__init__(config, **dependencies)
        self.api_base_url = "https://www.wienerlinien.at/ogd_realtime/monitor"
    
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
                    'rbl_numbers': ['254'],  # Karlsplatz and Wittelsbachstraße
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

        try:
            # Make single API request for all RBL numbers
            rbl_params = ','.join(all_rbl_numbers)
            url = f"{self.api_base_url}?rbl={rbl_params}&sender=vienna_transport_plugin"

            logger.info(f"Fetching data for {len(all_rbl_numbers)} RBL numbers in single request")
            response = requests.get(url, timeout=15)  # Increased timeout for larger response
            response.raise_for_status()

            data = response.json()

            # Process the combined response
            return self._process_combined_response(data, rbl_to_stop_mapping, stops_config)

        except Exception as e:
            logger.error(f"Error fetching combined departure data: {e}")
            return []

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

                            # Process departures
                            if 'departures' in line_info and 'departure' in line_info['departures']:
                                departures = line_info['departures']['departure']
                                if not isinstance(departures, list):
                                    departures = [departures]

                                for departure in departures[:2]:  # Limit to first 2 departures
                                    direction = departure.get('vehicle', {}).get('towards', 'Unknown Direction')
                                    countdown = departure.get('departureTime', {}).get('countdown', None)

                                    if direction not in stop_data[stop_id]['lines'][line_name]:
                                        stop_data[stop_id]['lines'][line_name][direction] = []

                                    # Add countdown time
                                    if countdown is not None:
                                        if countdown == 0:
                                            time_display = "*"
                                        else:
                                            time_display = str(countdown)

                                        stop_data[stop_id]['lines'][line_name][direction].append(time_display)

        except Exception as e:
            logger.error(f"Error processing combined API response: {e}")

        # Convert to departure_data format and sort/limit departures
        # Preserve the order from stops_config by iterating in that order
        departure_data = []
        for stop_id in stops_config.keys():
            if stop_id in stop_data:
                data_for_stop = stop_data[stop_id]
                if data_for_stop['lines'] and data_for_stop['name']:
                    # Sort and limit departures per direction to 2
                    for line_name in data_for_stop['lines']:
                        for direction in data_for_stop['lines'][line_name]:
                            def sort_key(time_str):
                                if time_str == "*":
                                    return 0
                                try:
                                    return int(time_str)
                                except:
                                    return 999

                            data_for_stop['lines'][line_name][direction].sort(key=sort_key)
                            data_for_stop['lines'][line_name][direction] = data_for_stop['lines'][line_name][direction][:2]

                    departure_data.append(data_for_stop)

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

            # Fallback to default fonts if get_font returns None
            if line_name_font is None:
                line_name_font = ImageFont.load_default()
            if direction_font is None:
                direction_font = ImageFont.load_default()
            if time_font is None:
                time_font = ImageFont.load_default()
        except Exception as e:
            logger.error(f"Error loading fonts: {e}")
            line_name_font = ImageFont.load_default()
            direction_font = ImageFont.load_default()
            time_font = ImageFont.load_default()

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
            for line_name, directions in stop['lines'].items():
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

                # Draw each direction
                direction_y_offset = 0
                for direction, times in directions.items():
                    if direction_y_offset + direction_line_height > row_height:
                        break  # Not enough space in this row

                    direction_row_y = directions_y + direction_y_offset + direction_line_height // 2

                    # Draw direction name
                    draw.text((directions_x, direction_row_y), direction, fill=text_color, font=direction_font)

                    # Calculate position for times (after direction name)
                    direction_bbox = draw.textbbox((0, 0), direction, font=direction_font)
                    direction_width = direction_bbox[2] - direction_bbox[0]
                    times_x = directions_x + direction_width + 20

                    # Draw departure times (right-aligned)
                    if times and len(times) > 0:
                        # Format times with consistent width and proper spacing
                        formatted_times = []
                        times_to_show = times[:2]  # Only show 2 times

                        for i, time in enumerate(times_to_show):
                            if time == "*":
                                # For asterisk, use consistent width
                                formatted_times.append("   *    ")
                            else:
                                # For numbers, use consistent width
                                if i == len(times_to_show) - 1:  # Last time gets 'min'
                                    formatted_times.append(f"{time:>3} min")
                                else:
                                    formatted_times.append(f"{time:>3}    ")  # Same width as " min"

                        # Join times with separators
                        times_text = " | ".join(formatted_times)

                        # Calculate right-aligned position
                        times_bbox = draw.textbbox((0, 0), times_text, font=time_font)
                        times_width = times_bbox[2] - times_bbox[0]
                        right_aligned_x = width - side_padding - times_width

                        # Draw the times
                        draw.text((right_aligned_x, direction_row_y), times_text, fill=text_color, font=time_font)

                    direction_y_offset += direction_line_height

                # Draw horizontal border after this line row
                current_y += row_height
                if current_y < height:
                    draw.line([side_padding, current_y, width - side_padding, current_y],
                            fill=border_color, width=1)
                current_y += 1  # Small gap after border

        return image
    
