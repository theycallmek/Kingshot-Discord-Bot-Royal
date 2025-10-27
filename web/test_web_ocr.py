"""
Quick test of the updated PaddleOCR implementation in web app
"""
from paddleocr import PaddleOCR
from pathlib import Path
import sys
import os

# Add parent directory to path to import from web module
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import the updated function
from web.app import extract_player_scores_from_ocr

def test_extract_function():
    """Test the extract_player_scores_from_ocr function with PaddleOCR"""

    # Initialize PaddleOCR
    print("Initializing PaddleOCR...")
    ocr = PaddleOCR(lang='en')

    # Test with one image
    test_image = Path("web/temp/Screenshot_2025.10.24_23.54.34.409.png")

    if not test_image.exists():
        print(f"Error: Test image not found: {test_image}")
        return

    print(f"\nProcessing: {test_image.name}")
    print("="*80)

    # Run OCR
    results = ocr.predict(str(test_image))

    # Extract player data
    player_data = extract_player_scores_from_ocr(results, test_image.name)

    # Display results
    print(f"\nExtracted {len(player_data)} players:")
    print("-"*80)

    for i, player in enumerate(player_data, 1):
        print(f"\n{i}. {player['player_name']}")
        print(f"   Ranking: {player.get('ranking', 'N/A')}")
        print(f"   Damage Points: {player.get('damage_points', 'N/A'):,}" if player.get('damage_points') else "   Damage Points: N/A")
        print(f"   Confidence: {player['confidence']:.2f}")
        print(f"   Source: {player['image_source']}")

    print("\n" + "="*80)
    print(f"Total players detected: {len(player_data)}")

    # Verify we got the expected players
    player_names = [p['player_name'] for p in player_data]
    print(f"\nPlayer names found: {player_names}")

    # Check if we detected [DOA] players
    doa_players = [p for p in player_data if p['player_name'].startswith('[DOA]')]
    print(f"\n[DOA] players detected: {len(doa_players)}")

    # Check if rankings were found
    players_with_rank = [p for p in player_data if p.get('ranking')]
    print(f"Players with ranking: {len(players_with_rank)}")

    # Check if damage points were found
    players_with_damage = [p for p in player_data if p.get('damage_points')]
    print(f"Players with damage points: {len(players_with_damage)}")

    return player_data

if __name__ == "__main__":
    test_extract_function()
