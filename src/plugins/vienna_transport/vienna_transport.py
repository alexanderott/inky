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
                    'rbl_numbers': ['266', '281'],  # Stubentor and St. Marx
                    'lines': '74A'  # Optional filter for specific lines
                },
                'Neulinggasse': {
                    'rbl_numbers': ['266', '281'],  # Stubentor and St. Marx
                    'lines': '74A'  # Optional filter for specific lines
                },
                'Eslarngasse': {
                    'rbl_numbers': ['266', '281'],  # Stubentor and St. Marx
                    'lines': '74A'  # Optional filter for specific lines
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
        """Fetch departure data for all configured stops."""
        departure_data = []
        
        for stop_id, stop_info in stops_config.items():
            try:
                rbl_numbers = stop_info.get('rbl_numbers', [])
                monitored_lines = stop_info.get('lines', '').strip()
                
                if not rbl_numbers:
                    logger.warning(f"No RBL numbers configured for stop group: {stop_id}")
                    continue
                
                # Parse monitored lines
                line_filter = []
                if monitored_lines:
                    line_filter = [line.strip().upper() for line in monitored_lines.split(',')]
                
                # Initialize combined stop data - name will be set from first API response
                combined_stop_data = {
                    'name': None,
                    'lines': {}
                }
                
                # Fetch data for each RBL number (direction) at this stop
                for rbl_number in rbl_numbers:
                    rbl_number = rbl_number.strip()
                    
                    if not rbl_number:
                        logger.warning(f"Empty RBL number in stop group: {stop_id}")
                        continue
                    
                    try:
                        # Fetch data from Wiener Linien API
                        url = f"{self.api_base_url}?rbl={rbl_number}&sender=vienna_transport_plugin"
                        response = requests.get(url, timeout=10)
                        response.raise_for_status()

                        data = response.json()

                        # Log the API response to console
                        print(f"\n=== Vienna Transport API Response for RBL {rbl_number} ===")
                        print(f"URL: {url}")
                        print(f"Response JSON:")
                        print(json.dumps(data, indent=2, ensure_ascii=False))
                        print("=" * 60)
                        
                        # Parse response for this specific RBL
                        rbl_data = self._parse_api_response(data, line_filter)
                        
                        # Set the stop name from the first successful API response
                        if combined_stop_data['name'] is None and rbl_data['name']:
                            combined_stop_data['name'] = rbl_data['name']
                        
                        # Merge this RBL's data into the combined stop data
                        self._merge_rbl_data(combined_stop_data, rbl_data)
                        
                    except Exception as e:
                        logger.error(f"Error fetching data for RBL {rbl_number} in stop group {stop_id}: {e}")
                        continue
                
                # Only add stop if there are departures and we got a name
                if combined_stop_data['lines'] and combined_stop_data['name']:
                    departure_data.append(combined_stop_data)
                    
            except Exception as e:
                logger.error(f"Error processing stop group {stop_id}: {e}")
                continue
        
        return departure_data
    
    def _parse_api_response(self, data, line_filter):
        """Parse the Wiener Linien API response."""
        stop_data = {
            'name': None,
            'lines': {}
        }
        
        try:
            # Navigate through the JSON structure
            if 'data' in data and 'monitors' in data['data']:
                monitors = data['data']['monitors']
                
                for monitor in monitors:
                    # Extract stop name from the first monitor
                    if stop_data['name'] is None and 'locationStop' in monitor:
                        stop_data['name'] = monitor['locationStop'].get('properties', {}).get('title', 'Unknown Stop')
                    
                    if 'lines' in monitor:
                        for line_info in monitor['lines']:
                            line_name = line_info.get('name', '').strip()
                            
                            # Filter lines if specified
                            if line_filter and line_name.upper() not in line_filter:
                                continue
                            
                            if line_name not in stop_data['lines']:
                                stop_data['lines'][line_name] = {}
                            
                            # Process departures
                            if 'departures' in line_info and 'departure' in line_info['departures']:
                                departures = line_info['departures']['departure']
                                if not isinstance(departures, list):
                                    departures = [departures]
                                
                                for departure in departures[:10]:  # Limit to first 10 departures
                                    # Get direction from API
                                    direction = departure.get('vehicle', {}).get('towards', 'Unknown Direction')
                                    countdown = departure.get('departureTime', {}).get('countdown', None)
                                    
                                    if direction not in stop_data['lines'][line_name]:
                                        stop_data['lines'][line_name][direction] = []
                                    
                                    # Add countdown time (convert to display format)
                                    if countdown is not None:
                                        if countdown == 0:
                                            time_display = "*"
                                        else:
                                            time_display = f"{countdown}min"
                                        
                                        stop_data['lines'][line_name][direction].append(time_display)
        
        except Exception as e:
            logger.error(f"Error parsing API response: {e}")
        
        # Sort and limit departures per direction to 2
        for line_name in stop_data['lines']:
            for direction in stop_data['lines'][line_name]:
                stop_data['lines'][line_name][direction] = stop_data['lines'][line_name][direction][:2]
        
        return stop_data
    
    def _merge_rbl_data(self, combined_data, rbl_data):
        """Merge data from a single RBL into the combined stop data."""
        for line_name, directions in rbl_data['lines'].items():
            if line_name not in combined_data['lines']:
                combined_data['lines'][line_name] = {}
            
            for direction, departures in directions.items():
                if direction not in combined_data['lines'][line_name]:
                    combined_data['lines'][line_name][direction] = []
                
                # Add departures from this RBL to the combined data
                combined_data['lines'][line_name][direction].extend(departures)
                
                # Sort by departure time and limit to 2 per direction
                # Convert times back to minutes for sorting, then back to display format
                def sort_key(time_str):
                    if time_str == "*":
                        return 0
                    try:
                        return int(time_str.replace("min", ""))
                    except:
                        return 999
                
                combined_data['lines'][line_name][direction].sort(key=sort_key)
                combined_data['lines'][line_name][direction] = combined_data['lines'][line_name][direction][:2]

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

        # Define font sizes and load fonts
        try:
            line_name_font = get_font("Jost", 42, "bold")  # Large bold font for line name
            direction_font = get_font("Jost", 28, "normal")  # Larger font for directions
            time_font = get_font("Jost", 16, "bold")  # Bold font for times

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
        line_square_bg = '#1976D2'  # Blue for line squares
        text_color = '#000000'      # Black text
        border_color = '#CCCCCC'    # Light gray borders

        # Layout constants
        margin = 10
        line_square_size = 60
        gap_after_square = 15
        row_height = 80
        direction_spacing = 25

        current_y = margin

        # Draw each stop's data
        for stop in departure_data:
            if not stop.get('lines'):
                continue

            # Draw each line for this stop
            for line_name, directions in stop['lines'].items():
                if current_y + row_height > height - margin:
                    break  # Not enough space for more rows

                # Draw line square on the left
                square_x = margin
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
                text_y = square_y + (line_square_size - text_height) // 2
                draw.text((text_x, text_y), line_name, fill='white', font=line_name_font)

                # Draw directions area starting after the gap
                directions_x = square_x + line_square_size + gap_after_square
                directions_y = current_y

                # Draw each direction
                direction_y_offset = 0
                for direction, times in directions.items():
                    if direction_y_offset + direction_spacing > row_height:
                        break  # Not enough space in this row

                    direction_row_y = directions_y + direction_y_offset + direction_spacing // 2

                    # Draw direction name
                    draw.text((directions_x, direction_row_y), direction, fill=text_color, font=direction_font)

                    # Calculate position for times (after direction name)
                    direction_bbox = draw.textbbox((0, 0), direction, font=direction_font)
                    direction_width = direction_bbox[2] - direction_bbox[0]
                    times_x = directions_x + direction_width + 20

                    # Draw departure times
                    if times and len(times) > 0:
                        # First time
                        first_time = times[0] if times[0] else "?"
                        draw.text((times_x, direction_row_y), first_time, fill=text_color, font=time_font)

                        # Calculate position for vertical separator and second time
                        first_time_bbox = draw.textbbox((0, 0), first_time, font=time_font)
                        first_time_width = first_time_bbox[2] - first_time_bbox[0]
                        separator_x = times_x + first_time_width + 15

                        # Draw vertical separator
                        draw.line([separator_x, direction_row_y, separator_x, direction_row_y + 15],
                                fill=border_color, width=1)

                        # Second time (if available)
                        if len(times) > 1:
                            second_time = times[1] if times[1] else "?"
                            second_time_x = separator_x + 15
                            draw.text((second_time_x, direction_row_y), second_time, fill=text_color, font=time_font)

                    direction_y_offset += direction_spacing

                # Draw horizontal border after this line row
                current_y += row_height
                if current_y < height - margin:
                    draw.line([margin, current_y, width - margin, current_y],
                            fill=border_color, width=1)
                current_y += 1  # Small gap after border

        return image
    
