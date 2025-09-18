import inspect
import importlib
import logging
import sys

from display.abstract_display import AbstractDisplay
from PIL import Image
from pathlib import Path
from plugins.plugin_registry import get_plugin_instance

logger = logging.getLogger(__name__)

class WaveshareDisplay(AbstractDisplay):
    """
    Handles Waveshare e-paper display dynamically based on device type.

    This class loads the appropriate display driver dynamically based on the 
    `display_type` specified in the device configuration, allowing support for 
    multiple Waveshare EPD models.  

    The module drivers are in display.waveshare_epd.
    """

    def initialize_display(self):
        
        """
        Initializes the Waveshare display device.

        Retrieves the display type from the device configuration and dynamically 
        loads the corresponding Waveshare EPD driver from display.waveshare_epd.

        Raises:
            ValueError: If `display_type` is missing or the specified module is 
                        not found.
        """
        
        logger.info("Initializing Waveshare display")

        # get the device type which should be the model number of the device.
        display_type = self.device_config.get_config("display_type")  
        logger.info(f"Loading EPD display for {display_type} display")

        if not display_type:
            raise ValueError("Waveshare driver but 'display_type' not specified in configuration.")

        # Construct module path dynamically - e.g. "display.waveshare_epd.epd7in3e"
        module_name = f"display.waveshare_epd.{display_type}" 

        # Workaround for some Waveshare drivers using 'import epdconfig' causing import errors
        epd_dir = Path(__file__).parent / "waveshare_epd"
        if str(epd_dir) not in sys.path:
            sys.path.insert(0, str(epd_dir))

        try:
            # Dynamically load module
            epd_module = importlib.import_module(module_name)  
            self.epd_display = epd_module.EPD()
            # Workaround for init functions with inconsistent casing
            self.epd_display_init = getattr(self.epd_display, "Init", getattr(self.epd_display, "init", None))

            if not callable(self.epd_display_init):
                raise AttributeError("No Init/init method found")

            self.epd_display_init()

            display_args_spec = inspect.getfullargspec(self.epd_display.display)
            display_args = display_args_spec.args
        except ModuleNotFoundError:
            raise ValueError(f"Unsupported Waveshare display type: {display_type}")
        except AttributeError:
            raise ValueError(f"Display does not support required methods: {display_type}")

        self.bi_color_display = len(display_args_spec.args) > 2

        # update the resolution directly from the loaded device context
        if not self.device_config.get_config("resolution"):
            w, h = int(self.epd_display.width), int(self.epd_display.height)
            resolution = [w, h] if w >= h else [h, w]
            self.device_config.update_value(
                "resolution",
                resolution,
                write=True)


    def display_image(self, image, image_settings=[], partial_refresh=False):

        """
        Displays an image on the Waveshare display.

        The image has been processed by adjusting orientation, resizing, and converting it
        into the buffer format required for e-paper rendering.

        Args:
            image (PIL.Image): The image to be displayed.
            image_settings (list, optional): Additional settings to modify image rendering.
            partial_refresh (bool, optional): Whether to use partial refresh if supported. Defaults to False.

        Raises:
            ValueError: If no image is provided.
        """

        if partial_refresh:
            logger.info("WAVESHARE_PARTIAL: Attempting partial refresh on Waveshare display")
        else:
            logger.info("WAVESHARE_FULL: Performing full refresh on Waveshare display")

        if not image:
            raise ValueError(f"No image provided.")

        # Assume device was in sleep mode.
        self.epd_display_init()

        # Some displays may need a different init for partial refresh
        if partial_refresh and hasattr(self.epd_display, 'Init_Partial'):
            logger.info("PARTIAL_INIT: Using specialized Init_Partial initialization")
            self.epd_display.Init_Partial()
        elif partial_refresh and hasattr(self.epd_display, 'init_partial'):
            logger.info("PARTIAL_INIT: Using specialized init_partial initialization")
            self.epd_display.init_partial()
        elif partial_refresh:
            logger.info("PARTIAL_INIT: No specialized partial init found, using standard init")

        if partial_refresh:
            # Try to use partial refresh if the display supports it
            partial_methods = [
                ('DisplayPartial', 'DisplayPartial'),
                ('displayPart', 'displayPart'),
                ('display_partial', 'display_partial'),
                ('DisplayPart', 'DisplayPart'),
                ('PartialUpdate', 'PartialUpdate'),
                ('partialUpdate', 'partialUpdate'),
                ('DisplayPartBase', 'DisplayPartBase')
            ]

            partial_method_found = False
            logger.info(f"PARTIAL_DETECTION: Checking for partial refresh methods on {type(self.epd_display).__name__}")

            for method_name, log_name in partial_methods:
                if hasattr(self.epd_display, method_name):
                    logger.info(f"PARTIAL_METHOD_FOUND: {log_name} method detected")
                    try:
                        import time
                        start_time = time.time()
                        method = getattr(self.epd_display, method_name)
                        logger.info(f"PARTIAL_EXECUTING: Calling {log_name} method...")
                        method(self.epd_display.getbuffer(image))
                        refresh_time = time.time() - start_time
                        logger.info(f"PARTIAL_SUCCESS: {log_name} completed in {refresh_time:.2f} seconds")
                        partial_method_found = True
                        break
                    except Exception as e:
                        logger.error(f"PARTIAL_FAILED: {log_name} method failed: {e}")
                        continue
                else:
                    logger.debug(f"PARTIAL_NOT_FOUND: {log_name} method not available")

            if not partial_method_found:
                logger.warning("PARTIAL_FALLBACK: No working partial refresh methods found, falling back to full refresh")
                partial_refresh = False

        if not partial_refresh:
            # Full refresh
            import time
            full_start_time = time.time()
            logger.info("FULL_REFRESH_START: Beginning full refresh sequence")

            # Clear residual pixels before updating the image.
            logger.info("FULL_REFRESH_CLEAR: Clearing display")
            self.epd_display.Clear()

            # Display the image on the WS display.
            logger.info("FULL_REFRESH_DISPLAY: Updating display with new image")
            if not self.bi_color_display:
                self.epd_display.display(self.epd_display.getbuffer(image))
            else:
                color_image = Image.new('1', image.size, 255)
                self.epd_display.display(
                    self.epd_display.getbuffer(image),
                    self.epd_display.getbuffer(color_image)
                )

            full_refresh_time = time.time() - full_start_time
            logger.info(f"FULL_REFRESH_COMPLETE: Full refresh completed in {full_refresh_time:.2f} seconds")

        # Put device into low power mode (EPD displays maintain image when powered off)
        logger.info("Putting Waveshare display into sleep mode for power saving.")
        self.epd_display.sleep()
