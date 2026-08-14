"""
Module 2 - Part 1: Screenshot Test
Purpose: Confirm we can capture what's currently on screen and save it as an image file.
This is the most basic form of "perception" - just seeing the screen.

How to run:
1. Activate your venv: venv\\Scripts\\activate
2. Run: python module2_screenshot_test.py
3. After it runs, open the file "screenshot_test.png" that gets created in this same
   folder, and check that it actually shows your current screen.
"""

import mss
import mss.tools


def take_screenshot(save_path: str = "screenshot_test.png"):
    """
    Captures the entire screen and saves it as a PNG file.
    """
    with mss.mss() as sct:
        # sct.monitors[0] = all monitors combined
        # sct.monitors[1] = just the primary monitor (most common choice)
        monitor = sct.monitors[1]

        screenshot = sct.grab(monitor)

        mss.tools.to_png(screenshot.rgb, screenshot.size, output=save_path)

    print(f"Screenshot saved to: {save_path}")
    print(f"Image size: {screenshot.size}")


if __name__ == "__main__":
    print("Taking a screenshot of your current screen...\n")
    take_screenshot()
    print("\nOpen the saved PNG file and confirm it matches what's on your screen right now.")
