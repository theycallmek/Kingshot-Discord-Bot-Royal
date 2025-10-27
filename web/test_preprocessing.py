"""
Test different image preprocessing techniques for OCR improvement
"""
import cv2
import numpy as np
from pathlib import Path
from paddleocr import PaddleOCR

# Test image
INPUT_DIR = Path("web/temp")
OUTPUT_DIR = Path("web/temp/preprocessing_tests")
OUTPUT_DIR.mkdir(exist_ok=True)

def preprocess_method_1_sharpen(image):
    """Method 1: Sharpening filter to enhance edges and text clarity"""
    # Create sharpening kernel
    kernel = np.array([[-1,-1,-1],
                       [-1, 9,-1],
                       [-1,-1,-1]])
    sharpened = cv2.filter2D(image, -1, kernel)
    return sharpened

def preprocess_method_2_contrast(image):
    """Method 2: Increase contrast using CLAHE (Contrast Limited Adaptive Histogram Equalization)"""
    # Convert to LAB color space
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)

    # Apply CLAHE to L channel
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
    l = clahe.apply(l)

    # Merge and convert back
    lab = cv2.merge([l, a, b])
    enhanced = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    return enhanced

def preprocess_method_3_denoise_sharpen(image):
    """Method 3: Denoise then sharpen for cleaner text"""
    # Denoise using bilateral filter (preserves edges)
    denoised = cv2.bilateralFilter(image, 9, 75, 75)

    # Then sharpen
    kernel = np.array([[0, -1, 0],
                       [-1, 5,-1],
                       [0, -1, 0]])
    sharpened = cv2.filter2D(denoised, -1, kernel)
    return sharpened

def preprocess_method_4_upscale_sharpen(image):
    """Method 4: Upscale 2x then sharpen (helps with small text)"""
    # Upscale using bicubic interpolation
    height, width = image.shape[:2]
    upscaled = cv2.resize(image, (width * 2, height * 2), interpolation=cv2.INTER_CUBIC)

    # Apply unsharp mask
    gaussian = cv2.GaussianBlur(upscaled, (0, 0), 2.0)
    sharpened = cv2.addWeighted(upscaled, 1.5, gaussian, -0.5, 0)

    return sharpened

def preprocess_method_5_adaptive_threshold(image):
    """Method 5: Convert to grayscale with adaptive thresholding (for high contrast text)"""
    # Convert to grayscale
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Apply adaptive thresholding
    thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, 11, 2)

    # Convert back to BGR for consistency
    result = cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)
    return result

def test_ocr_on_image(image_path, method_name, preprocessed_image):
    """Run OCR on preprocessed image and return detected text count"""
    # Save preprocessed image
    output_path = OUTPUT_DIR / f"{image_path.stem}_{method_name}.png"
    cv2.imwrite(str(output_path), preprocessed_image)

    # Run OCR
    ocr = PaddleOCR(lang='en')
    results = ocr.predict(str(output_path))

    if results and results[0]:
        texts = results[0]['rec_texts']
        scores = results[0]['rec_scores']

        # Count detected elements
        total_detected = len(texts)
        high_confidence = sum(1 for s in scores if s > 0.9)
        player_names = sum(1 for t in texts if '[DOA]' in t)
        damage_points = sum(1 for t in texts if 'damage' in t.lower() and 'point' in t.lower())

        return {
            'total_detected': total_detected,
            'high_confidence': high_confidence,
            'player_names': player_names,
            'damage_points': damage_points,
            'avg_confidence': sum(scores) / len(scores) if scores else 0
        }

    return {
        'total_detected': 0,
        'high_confidence': 0,
        'player_names': 0,
        'damage_points': 0,
        'avg_confidence': 0
    }

def main():
    # Find all test images
    test_images = sorted(list(INPUT_DIR.glob("Screenshot_*.png")))[:3]
    if not test_images:
        print("No test images found!")
        return

    print(f"Testing preprocessing methods on {len(test_images)} images")
    print("="*80)

    # Test each preprocessing method
    methods = [
        ("0_original", lambda x: x, "Original (no preprocessing)"),
        ("1_sharpen", preprocess_method_1_sharpen, "Sharpening filter"),
        ("2_contrast", preprocess_method_2_contrast, "CLAHE contrast enhancement"),
        ("3_denoise_sharpen", preprocess_method_3_denoise_sharpen, "Denoise + Sharpen"),
        ("4_upscale_sharpen", preprocess_method_4_upscale_sharpen, "2x Upscale + Unsharp mask"),
        ("5_adaptive_threshold", preprocess_method_5_adaptive_threshold, "Adaptive thresholding")
    ]

    # Aggregate results across all images
    aggregate_results = {description: {
        'total_detected': 0,
        'high_confidence': 0,
        'player_names': 0,
        'damage_points': 0,
        'avg_confidence': 0,
        'count': 0
    } for _, _, description in methods}

    # Test each image
    for img_idx, test_image in enumerate(test_images, 1):
        print(f"\n{'#'*80}")
        print(f"# IMAGE {img_idx}: {test_image.name}")
        print(f"{'#'*80}")

        # Load original image
        original = cv2.imread(str(test_image))

        # Test each preprocessing method
        for i, (method_name, method_func, description) in enumerate(methods):
            print(f"\n{i}. {description.upper()}")
            print(f"   Saved: {test_image.stem}_{method_name}.png")

            # Apply preprocessing
            preprocessed = method_func(original)

            # Test OCR
            stats = test_ocr_on_image(test_image, method_name, preprocessed)

            # Aggregate stats
            aggregate_results[description]['total_detected'] += stats['total_detected']
            aggregate_results[description]['high_confidence'] += stats['high_confidence']
            aggregate_results[description]['player_names'] += stats['player_names']
            aggregate_results[description]['damage_points'] += stats['damage_points']
            aggregate_results[description]['avg_confidence'] += stats['avg_confidence']
            aggregate_results[description]['count'] += 1

            print(f"   Total detected: {stats['total_detected']}")
            print(f"   High confidence (>0.9): {stats['high_confidence']}")
            print(f"   Player names: {stats['player_names']}")
            print(f"   Damage points: {stats['damage_points']}")
            print(f"   Avg confidence: {stats['avg_confidence']:.3f}")

    # Calculate averages
    for method_stats in aggregate_results.values():
        count = method_stats['count']
        if count > 0:
            method_stats['avg_confidence'] /= count

    # Summary
    print("\n" + "="*80)
    print(f"AGGREGATE RESULTS ACROSS {len(test_images)} IMAGES")
    print("="*80)

    print(f"\n{'Method':<40} {'Avg Conf':>10} {'Hi Conf':>10} {'Players':>10} {'Damage':>10}")
    print("-"*80)
    for _, _, description in methods:
        stats = aggregate_results[description]
        print(f"{description:<40} {stats['avg_confidence']:>10.3f} {stats['high_confidence']:>10} {stats['player_names']:>10} {stats['damage_points']:>10}")

    print("\n" + "="*80)
    print("BEST PERFORMERS:")
    print("="*80)

    # Find best by average confidence
    best_confidence = max(aggregate_results.items(), key=lambda x: x[1]['avg_confidence'])
    print(f"\nHighest average confidence: {best_confidence[0]}")
    print(f"  Avg confidence: {best_confidence[1]['avg_confidence']:.3f}")

    # Find best by high confidence count
    best_high_conf = max(aggregate_results.items(), key=lambda x: x[1]['high_confidence'])
    print(f"\nMost high-confidence detections: {best_high_conf[0]}")
    print(f"  High confidence count: {best_high_conf[1]['high_confidence']}")

    # Find best by damage points
    best_damage = max(aggregate_results.items(), key=lambda x: x[1]['damage_points'])
    print(f"\nMost damage points detected: {best_damage[0]}")
    print(f"  Damage points: {best_damage[1]['damage_points']}")

    print(f"\n{'='*80}")
    print(f"All preprocessed images saved to: {OUTPUT_DIR}")
    print(f"{'='*80}")

if __name__ == "__main__":
    main()
