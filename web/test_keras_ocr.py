"""
Test script to compare Keras-OCR vs EasyOCR on attendance screenshots
"""
import keras_ocr
import easyocr
import os
from pathlib import Path
import time

# Path to test images
TEMP_DIR = Path("web/temp")

def test_keras_ocr(image_path):
    """Test Keras-OCR on a single image"""
    print(f"\n{'='*80}")
    print(f"Testing Keras-OCR on: {image_path.name}")
    print(f"{'='*80}")

    # Initialize Keras-OCR pipeline
    print("Initializing Keras-OCR pipeline...")
    start_time = time.time()
    pipeline = keras_ocr.pipeline.Pipeline()
    init_time = time.time() - start_time
    print(f"Initialization time: {init_time:.2f}s")

    # Read and process image
    print("\nProcessing image...")
    start_time = time.time()
    prediction_groups = pipeline.recognize([str(image_path)])
    process_time = time.time() - start_time
    print(f"Processing time: {process_time:.2f}s")

    # Display results
    print("\n--- RAW KERAS-OCR OUTPUT ---")
    for word, box in prediction_groups[0]:
        # box is a 4x2 array of coordinates
        x_coords = [point[0] for point in box]
        y_coords = [point[1] for point in box]
        print(f"Text: '{word}' | BBox: ({min(x_coords):.1f}, {min(y_coords):.1f}, {max(x_coords):.1f}, {max(y_coords):.1f})")

    return prediction_groups[0]

def test_easyocr(image_path):
    """Test EasyOCR on a single image"""
    print(f"\n{'='*80}")
    print(f"Testing EasyOCR on: {image_path.name}")
    print(f"{'='*80}")

    # Initialize EasyOCR
    print("Initializing EasyOCR...")
    start_time = time.time()
    reader = easyocr.Reader(['en'], gpu=False, verbose=False)
    init_time = time.time() - start_time
    print(f"Initialization time: {init_time:.2f}s")

    # Read and process image
    print("\nProcessing image...")
    start_time = time.time()
    results = reader.readtext(str(image_path))
    process_time = time.time() - start_time
    print(f"Processing time: {process_time:.2f}s")

    # Display results
    print("\n--- RAW EASYOCR OUTPUT ---")
    for bbox, text, confidence in results:
        print(f"Text: '{text}' | Confidence: {confidence:.3f} | BBox: {bbox}")

    return results

def compare_outputs(keras_results, easyocr_results, image_name):
    """Compare the two OCR outputs side by side"""
    print(f"\n{'='*80}")
    print(f"COMPARISON FOR: {image_name}")
    print(f"{'='*80}")

    print("\n--- KERAS-OCR DETECTED TEXT (in order) ---")
    for i, (word, box) in enumerate(keras_results, 1):
        print(f"{i:2d}. {word}")

    print(f"\nTotal detected by Keras-OCR: {len(keras_results)} text elements")

    print("\n--- EASYOCR DETECTED TEXT (in order) ---")
    for i, (bbox, text, confidence) in enumerate(easyocr_results, 1):
        print(f"{i:2d}. {text} (confidence: {confidence:.2f})")

    print(f"\nTotal detected by EasyOCR: {len(easyocr_results)} text elements")

    # Look for [DOA] patterns
    print("\n--- LOOKING FOR [DOA] PATTERNS ---")
    print("Keras-OCR:")
    keras_doa = [word for word, box in keras_results if 'doa' in word.lower() or 'DOA' in word or '[' in word or ']' in word]
    for word in keras_doa:
        print(f"  - {word}")

    print("\nEasyOCR:")
    easy_doa = [text for bbox, text, conf in easyocr_results if 'doa' in text.lower() or 'DOA' in text or '[' in text or ']' in text]
    for text in easy_doa:
        print(f"  - {text}")

def main():
    # Find test images
    if not TEMP_DIR.exists():
        print(f"Error: {TEMP_DIR} does not exist!")
        return

    image_files = list(TEMP_DIR.glob("*.png")) + list(TEMP_DIR.glob("*.jpg")) + list(TEMP_DIR.glob("*.jpeg"))

    if not image_files:
        print(f"Error: No images found in {TEMP_DIR}")
        return

    print(f"Found {len(image_files)} test images")

    # Test each image with both OCR engines
    for image_path in sorted(image_files)[:3]:  # Limit to first 3 images
        print(f"\n\n{'#'*80}")
        print(f"# Processing: {image_path.name}")
        print(f"{'#'*80}")

        # Test Keras-OCR
        keras_results = test_keras_ocr(image_path)

        # Test EasyOCR
        easyocr_results = test_easyocr(image_path)

        # Compare
        compare_outputs(keras_results, easyocr_results, image_path.name)

        print(f"\n{'#'*80}\n")

if __name__ == "__main__":
    main()
