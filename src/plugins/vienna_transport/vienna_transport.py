import requests
import json
import logging
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from plugins.base_plugin.base_plugin import BasePlugin

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
            width = device_config.get_display_width()
            height = device_config.get_display_height()
            
            # Create image
            image = Image.new('RGB', (width, height), 'white')
            draw = ImageDraw.Draw(image)
            
            # Get stops configuration
            stops_config = settings.get('stops', {})
            if not stops_config:
                return self._create_error_image(width, height, "No stops configured")
            
            # Fetch departure data for all stops
            departure_data = self._fetch_departure_data(stops_config)
            
            # Render the departure information
            self._render_departures(draw, departure_data, width, height)
            
            return image
            
        except Exception as e:
            logger.error(f"Error generating Vienna transport image: {e}")
            return self._create_error_image(width or 800, height or 600, f"Error: {str(e)}")
    
    def _fetch_departure_data(self, stops_config):
        """Fetch departure data for all configured stops."""
        departure_data = []
        
        for stop_id, stop_info in stops_config.items():
            try:
                rbl_number = stop_info.get('rbl', '').strip()
                stop_name = stop_info.get('name', 'Unknown Stop')
                monitored_lines = stop_info.get('lines', '').strip()
                
                if not rbl_number:
                    logger.warning(f"No RBL number configured for stop: {stop_name}")
                    continue
                
                # Parse monitored lines
                line_filter = []
                if monitored_lines:
                    line_filter = [line.strip().upper() for line in monitored_lines.split(',')]
                
                # Fetch data from Wiener Linien API
                url = f"{self.api_base_url}?rbl={rbl_number}&sender=vienna_transport_plugin"
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                
                data = response.json()
                
                # Parse response
                stop_data = self._parse_api_response(data, stop_name, line_filter)
                if stop_data['lines']:  # Only add if there are departures
                    departure_data.append(stop_data)
                    
            except Exception as e:
                logger.error(f"Error fetching data for stop {stop_name}: {e}")
                continue
        
        return departure_data
    
    def _parse_api_response(self, data, stop_name, line_filter):
        """Parse the Wiener Linien API response."""
        stop_data = {
            'name': stop_name,
            'lines': {}
        }
        
        try:
            # Navigate through the JSON structure
            if 'data' in data and 'monitors' in data['data']:
                monitors = data['data']['monitors']
                
                for monitor in monitors:
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
                                    direction = departure.get('vehicle', {}).get('direction', 'Unknown')
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
            logger.error(f"Error parsing API response for {stop_name}: {e}")
        
        # Sort and limit departures per direction to 2
        for line_name in stop_data['lines']:
            for direction in stop_data['lines'][line_name]:
                stop_data['lines'][line_name][direction] = stop_data['lines'][line_name][direction][:2]
        
        return stop_data
    
    def _render_departures(self, draw, departure_data, width, height):
        """Render departure information on the image."""
        try:
            # Use system fonts
            title_font = ImageFont.load_default()
            normal_font = ImageFont.load_default()
            
            y_pos = 10
            line_height = 20
            
            # Title
            draw.text((10, y_pos), "Vienna Public Transport", fill='black', font=title_font)
            y_pos += line_height * 2
            
            # Current time
            current_time = datetime.now().strftime("%H:%M")
            draw.text((10, y_pos), f"Updated: {current_time}", fill='gray', font=normal_font)
            y_pos += line_height * 2
            
            # Render each stop
            for stop_data in departure_data:
                if y_pos > height - 50:  # Check if we're running out of space
                    break
                
                # Stop name
                draw.text((10, y_pos), f"🚏 {stop_data['name']}", fill='black', font=title_font)
                y_pos += line_height + 5
                
                # Lines and departures
                for line_name, directions in stop_data['lines'].items():
                    if y_pos > height - 30:
                        break
                    
                    # Line name
                    draw.text((20, y_pos), f"Line {line_name}:", fill='blue', font=normal_font)
                    y_pos += line_height
                    
                    # Directions
                    for direction, times in directions.items():
                        if y_pos > height - 20:
                            break
                        
                        times_str = ", ".join(times) if times else "No departures"
                        direction_short = direction[:25] + "..." if len(direction) > 25 else direction
                        
                        draw.text((30, y_pos), f"→ {direction_short}: {times_str}", fill='black', font=normal_font)
                        y_pos += line_height
                
                y_pos += 5  # Space between stops
            
            # If no data
            if not departure_data:
                draw.text((10, height//2), "No departure data available", fill='red', font=title_font)
                
        except Exception as e:
            logger.error(f"Error rendering departures: {e}")
            draw.text((10, 10), f"Render error: {str(e)}", fill='red', font=normal_font)
    
    def _create_error_image(self, width, height, error_message):
        """Create an error image with the given message."""
        image = Image.new('RGB', (width, height), 'white')
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()
        
        draw.text((10, 10), "Vienna Transport Error", fill='red', font=font)
        draw.text((10, 40), error_message, fill='black', font=font)
        
        return image