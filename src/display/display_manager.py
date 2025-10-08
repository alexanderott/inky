import fnmatch
import json
import logging

from utils.image_utils import resize_image, change_orientation, apply_image_enhancement
from display.mock_display import MockDisplay

logger = logging.getLogger(__name__)

# Try to import hardware displays, but don't fail if they're not available
try:
    from display.inky_display import InkyDisplay
except ImportError:
    logger.info("Inky display not available, hardware support disabled")

try:
    from display.waveshare_display import WaveshareDisplay
except ImportError:
    logger.info("Waveshare display not available, hardware support disabled")

class DisplayManager:

    """Manages the display and rendering of images."""

    def __init__(self, device_config):

        """
        Initializes the display manager and selects the correct display type
        based on the configuration.

        Args:
            device_config (object): Configuration object containing display settings.

        Raises:
            ValueError: If an unsupported display type is specified.
        """

        self.device_config = device_config
        self.partial_refresh_count = 0
        self.max_partial_refreshes = 5
     
        display_type = device_config.get_config("display_type", default="inky")

        if display_type == "mock":
            self.display = MockDisplay(device_config)
        elif display_type == "inky":
            self.display = InkyDisplay(device_config)
        elif fnmatch.fnmatch(display_type, "epd*in*"):  
            # derived from waveshare epd - we assume here that will be consistent
            # otherwise we will have to enshring the manufacturer in the 
            # display_type and then have a display_model parameter.  Will leave
            # that for future use if the need arises.
            #
            # see https://github.com/waveshareteam/e-Paper
            self.display = WaveshareDisplay(device_config)
        else:
            raise ValueError(f"Unsupported display type: {display_type}")

    def display_image(self, image, image_settings=[]):

        """
        Delegates image rendering to the appropriate display instance.

        Args:
            image (PIL.Image): The image to be displayed.
            image_settings (list, optional): List of settings to modify image rendering.

        Raises:
            ValueError: If no valid display instance is found.
        """

        if not hasattr(self, "display"):
            raise ValueError("No valid display instance initialized.")

        # Keep reference to original image to close it later
        original_image = image

        try:
            # Save the image
            logger.info(f"Saving image to {self.device_config.current_image_file}")
            image.save(self.device_config.current_image_file)

            # Resize and adjust orientation - each operation creates a new image
            # We need to track and close intermediate images to prevent FD leaks
            prev_image = image
            image = change_orientation(image, self.device_config.get_config("orientation"))
            if image is not prev_image and prev_image is not original_image:
                try:
                    prev_image.close()
                except:
                    pass

            prev_image = image
            image = resize_image(image, self.device_config.get_resolution(), image_settings)
            if image is not prev_image and prev_image is not original_image:
                try:
                    prev_image.close()
                except:
                    pass

            if self.device_config.get_config("inverted_image"):
                prev_image = image
                image = image.rotate(180)
                if image is not prev_image and prev_image is not original_image:
                    try:
                        prev_image.close()
                    except:
                        pass

            prev_image = image
            image = apply_image_enhancement(image, self.device_config.get_config("image_settings"))
            if image is not prev_image and prev_image is not original_image:
                try:
                    prev_image.close()
                except:
                    pass

            # Check if partial refresh is disabled in config
            partial_refresh_disabled = self.device_config.get_config("disable_partial_refresh", default=False)

            # Determine if we should use partial refresh
            # Counter 0 = full refresh, counters 1-5 = partial refresh, then reset to 0
            use_partial_refresh = (not partial_refresh_disabled and
                                 self.partial_refresh_count > 0 and
                                 self.partial_refresh_count <= self.max_partial_refreshes)

            if partial_refresh_disabled:
                logger.info("REFRESH_MODE: Full refresh (partial refresh disabled in configuration)")
            elif use_partial_refresh:
                self.partial_refresh_count += 1
                logger.info(f"REFRESH_MODE: Attempting partial refresh ({self.partial_refresh_count}/{self.max_partial_refreshes})")
            else:
                # Full refresh (either counter is 0 or limit reached), then set counter to 1
                if self.partial_refresh_count == 0:
                    logger.info("REFRESH_MODE: Full refresh (startup or counter at 0)")
                else:
                    logger.info("REFRESH_MODE: Full refresh (partial refresh limit reached)")
                self.partial_refresh_count = 1

            # Pass to the concrete instance to render to the device.
            import time
            refresh_start_time = time.time()
            logger.info(f"REFRESH_START: Beginning display refresh at {time.strftime('%H:%M:%S')}")

            self.display.display_image(image, image_settings, partial_refresh=use_partial_refresh)

            total_refresh_time = time.time() - refresh_start_time
            logger.info(f"REFRESH_COMPLETE: Display refresh completed in {total_refresh_time:.2f} seconds")
        finally:
            # Always close the final processed image and original image to prevent FD leaks
            if image is not original_image:
                try:
                    image.close()
                except:
                    pass
            try:
                original_image.close()
            except:
                pass