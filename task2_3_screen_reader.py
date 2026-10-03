"""
Task 2.3: Screen Content Reading & OCR Engine
Purpose: Read text from the live desktop screen (or a specific screen region)
using mss for capture and pytesseract for OCR — enabling the AI to 'read' what
it sees on screen for closed-loop verification.

Features:
1. Dynamic monitor selection — no hardcoded monitor index.
2. Dynamic region selection — specify top/left/width/height or use full screen.
3. Returns OCR text for downstream AI verification steps.
4. Saves the captured region image to a configurable output path.
5. Zero hardcoded coordinates, filenames, or monitor indices.

How to run:
1. Install dependencies: pip install mss pytesseract pillow
2. Install Tesseract OCR: https://github.com/UB-Mannheim/tesseract/wiki
3. Activate venv: .\\venv\\Scripts\\activate
4. Run: python task2_3_screen_reader.py
   Or with options:
     python task2_3_screen_reader.py --monitor 1
     python task2_3_screen_reader.py --region 0 0 800 600 --output region_snap.png
     python task2_3_screen_reader.py --find "word to locate"
"""

import os
import sys
import time
import argparse
from datetime import datetime
from typing import Optional

try:
    import mss
    import mss.tools
except ImportError:
    print("ERROR: 'mss' is not installed. Run: pip install mss")
    sys.exit(1)

try:
    from PIL import Image
except ImportError:
    print("ERROR: 'Pillow' is not installed. Run: pip install pillow")
    sys.exit(1)

try:
    import pytesseract
except ImportError:
    print("ERROR: 'pytesseract' is not installed. Run: pip install pytesseract")
    sys.exit(1)


def find_tesseract_cmd() -> str:
    """
    Dynamically locates the Tesseract OCR executable from common install paths
    and the system PATH — no hardcoded path.
    """
    # 1. Check if it's already on PATH
    import shutil
    which = shutil.which("tesseract")
    if which:
        return which

    # 2. Search common Windows installation directories dynamically
    candidate_roots = [
        os.environ.get("PROGRAMFILES", "C:\\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
        os.environ.get("APPDATA", ""),
    ]
    for root in candidate_roots:
        if not root:
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            if "tesseract.exe" in filenames:
                return os.path.join(dirpath, "tesseract.exe")
            # Stop descending deeper than 3 levels to avoid long walks
            depth = dirpath.replace(root, "").count(os.sep)
            if depth >= 3:
                dirnames.clear()

    return "tesseract"  # fallback: hope it's on PATH


def capture_screen_region(
    monitor_index: int = 1,
    region: Optional[dict] = None,
    output_path: Optional[str] = None,
) -> str:
    """
    Captures the screen or a specified region using mss.
    Returns the absolute path to the saved image file.

    Args:
        monitor_index: mss monitor index (0 = all combined, 1 = primary, 2+ = secondary).
        region: Optional dict with keys top, left, width, height (overrides monitor_index).
        output_path: Optional output file path. Auto-generates timestamped name if not given.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if not output_path:
        output_path = os.path.join(os.getcwd(), f"screen_read_{timestamp}.png")

    with mss.MSS() as sct:
        monitors = sct.monitors
        print(f"  Available monitors: {len(monitors) - 1} (monitor list: {monitors})")

        if region:
            capture_area = region
            print(f"  Capturing custom region: {capture_area}")
        else:
            if monitor_index >= len(monitors):
                print(f"  Monitor {monitor_index} not found. Falling back to primary (index 1).")
                monitor_index = 1
            capture_area = monitors[monitor_index]
            print(f"  Capturing monitor {monitor_index}: {capture_area}")

        screenshot = sct.grab(capture_area)
        mss.tools.to_png(screenshot.rgb, screenshot.size, output=output_path)

    print(f"  Saved capture: {output_path}")
    return output_path


def read_text_from_image(image_path: str, lang: str = "eng") -> str:
    """
    Runs Tesseract OCR on the captured image and returns extracted text.

    Args:
        image_path: Path to the PNG image file.
        lang: Tesseract language code (default 'eng' for English).
    """
    tesseract_cmd = find_tesseract_cmd()
    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    print(f"  Using Tesseract: {tesseract_cmd}")

    img = Image.open(image_path)
    text = pytesseract.image_to_string(img, lang=lang)
    return text.strip()


def find_text_on_screen(
    search_term: str,
    monitor_index: int = 1,
    region: Optional[dict] = None,
    output_path: Optional[str] = None,
    lang: str = "eng",
) -> dict:
    """
    Captures screen and checks whether a specific text string is present via OCR.
    Returns a result dict with found flag, extracted text, and image path.

    Args:
        search_term: Text to search for in the screen content.
        monitor_index: Monitor index to capture.
        region: Optional custom bounding box.
        output_path: Optional path to save the screenshot.
        lang: OCR language code.
    """
    print(f"\n--- Screen Content Search ---")
    print(f"  Searching for: '{search_term}'")

    img_path = capture_screen_region(monitor_index, region, output_path)
    ocr_text = read_text_from_image(img_path, lang=lang)

    found = search_term.lower() in ocr_text.lower()
    print(f"  OCR result (first 300 chars): '{ocr_text[:300]}'")
    print(f"  Text '{search_term}' found on screen: {found}")

    return {
        "found": found,
        "search_term": search_term,
        "ocr_text": ocr_text,
        "image_path": img_path,
    }


def run_task_2_3(
    monitor_index: int = 1,
    region: Optional[dict] = None,
    output_path: Optional[str] = None,
    find: Optional[str] = None,
    lang: str = "eng",
):
    """
    Main entry point for Task 2.3 - Screen Content Reading & OCR.
    """
    print("\n============================================================")
    print("Task 2.3: Screen Content Reading & OCR Engine")
    print("============================================================\n")

    if find:
        result = find_text_on_screen(find, monitor_index, region, output_path, lang)
        print(f"\nResult: Found='{result['found']}', Image='{result['image_path']}'")
        return result
    else:
        print("Capturing screen and extracting all visible text...")
        img_path = capture_screen_region(monitor_index, region, output_path)
        ocr_text = read_text_from_image(img_path, lang=lang)
        print(f"\n--- Extracted Screen Text ---\n{ocr_text[:1000]}")
        print(f"\nFull text saved to: {img_path} (image), OCR text above.")
        return {"ocr_text": ocr_text, "image_path": img_path}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="TaskForge Task 2.3 - Screen Content Reading & OCR Engine"
    )
    parser.add_argument(
        "--monitor",
        type=int,
        default=1,
        help="Monitor index to capture (0=all combined, 1=primary, 2+=secondary). Default: 1.",
    )
    parser.add_argument(
        "--region",
        type=int,
        nargs=4,
        metavar=("LEFT", "TOP", "WIDTH", "HEIGHT"),
        default=None,
        help="Custom region to capture: left top width height (e.g. --region 0 0 800 600).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output image file path. Auto-generates timestamped filename if not set.",
    )
    parser.add_argument(
        "--find",
        type=str,
        default=None,
        help="Text string to search for on screen via OCR.",
    )
    parser.add_argument(
        "--lang",
        type=str,
        default="eng",
        help="Tesseract OCR language code. Default: eng.",
    )

    args = parser.parse_args()

    region_dict = None
    if args.region:
        left, top, width, height = args.region
        region_dict = {"left": left, "top": top, "width": width, "height": height}

    run_task_2_3(
        monitor_index=args.monitor,
        region=region_dict,
        output_path=args.output,
        find=args.find,
        lang=args.lang,
    )
