import os
import logging
from datetime import datetime
from .abstract_display import AbstractDisplay

logger = logging.getLogger(__name__)

class MockDisplay(AbstractDisplay):
    """Mock display for development without hardware."""
    
    def __init__(self, device_config):
        self.device_config = device_config
        resolution = device_config.get_resolution()
        self.width = resolution[0]
        self.height = resolution[1]
        self.output_dir = device_config.get_config('output_dir', 'mock_display_output')
        os.makedirs(self.output_dir, exist_ok=True)
        
    def initialize_display(self):
        """Initialize mock display (no-op for development)."""
        logger.info(f"Mock display initialized: {self.width}x{self.height}")
        
    def display_image(self, image, image_settings=[], partial_refresh=False):
        refresh_type = "partial" if partial_refresh else "full"
        logger.info(f"Mock display: displaying image ({refresh_type} refresh)")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"display_{timestamp}_{refresh_type}.png"
        filepath = os.path.join(self.output_dir, filename)
        image.save(filepath, "PNG")

        # Also save as latest.png for convenience
        image.save(os.path.join(self.output_dir, 'latest.png'), "PNG")