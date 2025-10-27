"""
Test script to compare PaddleOCR vs EasyOCR on attendance screenshots
"""
from paddleocr import PaddleOCR
import easyocr
import os
from pathlib import Path
import time

# Path to test images
TEMP_DIR = Path(__file__).parent / "temp"

def test_paddle_ocr(image_path):
    """Test PaddleOCR on a single image"""
    print(f"\n{'='*80}")
    print(f"Testing PaddleOCR on: {image_path.name}")
    print(f"{'='*80}")

    # Initialize PaddleOCR
    print("Initializing PaddleOCR...")
    start_time = time.time()
    ocr = PaddleOCR(lang='en')
    init_time = time.time() - start_time
    print(f"Initialization time: {init_time:.2f}s")

    # Read and process image
    print("\nProcessing image...")
    start_time = time.time()
    result = ocr.predict(str(image_path))
    process_time = time.time() - start_time
    print(f"Processing time: {process_time:.2f}s")

    # Display results
    print("\n--- RAW PADDLEOCR OUTPUT ---")

    if result and isinstance(result, list) and len(result) > 0:
        # New PaddleOCR API returns a list with one dict containing rec_texts and rec_scores
        page_result = result[0]

        if 'rec_texts' in page_result and 'rec_scores' in page_result:
            texts = page_result['rec_texts']
            scores = page_result['rec_scores']
            polys = page_result.get('rec_polys', [])

            for i, (text, score) in enumerate(zip(texts, scores)):
                if polys and i < len(polys):
                    poly = polys[i]
                    x_coords = poly[:, 0]
                    y_coords = poly[:, 1]
                    print(f"Text: '{text}' | Confidence: {score:.3f} | BBox: ({min(x_coords):.1f}, {min(y_coords):.1f}, {max(x_coords):.1f}, {max(y_coords):.1f})")
                else:
                    print(f"Text: '{text}' | Confidence: {score:.3f}")

            # Return in compatible format
            return [(text, score, poly if i < len(polys) else None)
                    for i, (text, score, poly) in enumerate(zip(texts, scores, polys))]
        else:
            print("Unexpected result structure")
            return []

    print("No text detected!")
    return []

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

def compare_outputs(paddle_results, easyocr_results, image_name):
    """Compare the two OCR outputs side by side"""
    print(f"\n{'='*80}")
    print(f"COMPARISON FOR: {image_name}")
    print(f"{'='*80}")

    print("\n--- PADDLEOCR DETECTED TEXT (in order) ---")
    if paddle_results:
        for i, (text, confidence, poly) in enumerate(paddle_results, 1):
            print(f"{i:2d}. {text} (confidence: {confidence:.2f})")
        print(f"\nTotal detected by PaddleOCR: {len(paddle_results)} text elements")
    else:
        print("No text detected")
        print(f"\nTotal detected by PaddleOCR: 0 text elements")

    print("\n--- EASYOCR DETECTED TEXT (in order) ---")
    for i, (bbox, text, confidence) in enumerate(easyocr_results, 1):
        print(f"{i:2d}. {text} (confidence: {confidence:.2f})")

    print(f"\nTotal detected by EasyOCR: {len(easyocr_results)} text elements")

    # Look for [DOA] patterns
    print("\n--- LOOKING FOR [DOA] PATTERNS ---")
    print("PaddleOCR:")
    if paddle_results:
        paddle_doa = [text for text, conf, poly in paddle_results if 'doa' in text.lower() or 'DOA' in text or '[' in text or ']' in text]
        if paddle_doa:
            for text in paddle_doa:
                print(f"  - {text}")
        else:
            print("  (none found)")
    else:
        print("  (no text detected)")

    print("\nEasyOCR:")
    easy_doa = [text for bbox, text, conf in easyocr_results if 'doa' in text.lower() or 'DOA' in text or '[' in text or ']' in text]
    if easy_doa:
        for text in easy_doa:
            print(f"  - {text}")
    else:
        print("  (none found)")

    # Look for damage points
    print("\n--- LOOKING FOR DAMAGE POINTS ---")
    print("PaddleOCR:")
    if paddle_results:
        paddle_damage = [text for text, conf, poly in paddle_results if 'damage' in text.lower() or 'point' in text.lower()]
        if paddle_damage:
            for text in paddle_damage:
                print(f"  - {text}")
        else:
            print("  (none found)")
    else:
        print("  (no text detected)")

    print("\nEasyOCR:")
    easy_damage = [text for bbox, text, conf in easyocr_results if 'damage' in text.lower() or 'point' in text.lower()]
    if easy_damage:
        for text in easy_damage:
            print(f"  - {text}")
    else:
        print("  (none found)")

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

        # Test PaddleOCR
        paddle_results = test_paddle_ocr(image_path)

        # Test EasyOCR
        easyocr_results = test_easyocr(image_path)

        # Compare
        compare_outputs(paddle_results, easyocr_results, image_path.name)

        print(f"\n{'#'*80}\n")

if __name__ == "__main__":
    main()
