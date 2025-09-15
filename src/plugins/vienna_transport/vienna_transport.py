import requests
import json
import logging
from datetime import datetime
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
            dimensions = device_config.get_resolution()
            if device_config.get_config("orientation") == "vertical":
                dimensions = dimensions[::-1]
            
            # Simplified stops configuration - names and directions will be fetched from API
            stops_config = {
                'barichgasse': {
                    'rbl_numbers': ['266', '281'],  # Stubentor and St. Marx
                    'lines': '74A'  # Optional filter for specific lines
                }
            }
            
            # Fetch departure data for all stops
            departure_data = self._fetch_departure_data(stops_config)
            
            # Prepare template parameters
            template_params = {
                'departure_data': departure_data,
                'current_time': datetime.now().strftime("%H:%M"),
                'plugin_settings': settings
            }
            
            # Render using HTML template
            image = self.render_image(dimensions, "vienna_transport.html", "vienna_transport.css", template_params)
            if not image:
                raise RuntimeError("Failed to render image from template")
            
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
                                    direction = departure.get('vehicle', {}).get('direction', 'Unknown Direction')
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
    
