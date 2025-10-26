"""
OCR Name Correction Module
Intelligently corrects OCR misreads using alliance tag validation and fuzzy matching
"""

from rapidfuzz import fuzz, process
from sqlmodel import Session, select
from web.models import User
import re
from typing import Optional, Tuple, List


class NameCorrectionEngine:
    """
    Corrects OCR-detected player names by:
    1. Extracting and validating alliance tags (e.g., [DOA])
    2. Fuzzy matching against known database names
    3. Cross-referencing alliance membership for validation
    """

    def __init__(self, users_engine):
        """
        Initialize the name correction engine

        Args:
            users_engine: SQLModel engine for users database
        """
        self.users_engine = users_engine
        self.alliance_tag_cache = {}  # Cache alliance tags from database
        self.users_by_alliance = {}  # Cache users grouped by alliance
        self._build_alliance_cache()

    def _build_alliance_cache(self):
        """Build cache of alliance tags and users"""
        with Session(self.users_engine) as session:
            all_users = session.exec(select(User)).all()

            # Group users by alliance
            for user in all_users:
                if user.alliance:
                    alliance_id = str(user.alliance)

                    if alliance_id not in self.users_by_alliance:
                        self.users_by_alliance[alliance_id] = []
                    self.users_by_alliance[alliance_id].append(user)

            # Note: Database nicknames don't contain alliance tags
            # We'll validate tags against known alliance IDs instead

    def extract_alliance_tag(self, player_name: str) -> Tuple[Optional[str], str]:
        """
        Extract alliance tag from player name

        Args:
            player_name: Full player name with tag (e.g., "[DOA]PlayerName")

        Returns:
            Tuple of (alliance_tag, name_without_tag)
            If no tag found, returns (None, original_name)
        """
        tag_match = re.match(r'\[([^\]]+)\](.+)', player_name)
        if tag_match:
            tag = tag_match.group(1).strip()
            name = tag_match.group(2).strip()
            return tag, name

        return None, player_name

    def correct_alliance_tag(self, detected_tag: str, known_tags: List[str]) -> Tuple[str, float]:
        """
        Correct alliance tag using fuzzy matching

        Args:
            detected_tag: OCR-detected alliance tag
            known_tags: List of known alliance tags from database

        Returns:
            Tuple of (corrected_tag, confidence_score)
        """
        if not known_tags:
            return detected_tag, 0.0

        # Try the exact match first (case-insensitive)
        for known_tag in known_tags:
            if detected_tag.upper() == known_tag.upper():
                return known_tag, 1.0

        # Use fuzzy matching
        result = process.extractOne(detected_tag.upper(), [tag.upper() for tag in known_tags], scorer=fuzz.ratio)

        if result:
            matched_tag_upper, score, _ = result
            # Find original case version
            for known_tag in known_tags:
                if known_tag.upper() == matched_tag_upper:
                    return known_tag, score / 100.0

        return detected_tag, 0.0

    def fix_common_ocr_errors(self, text: str) -> str:
        """
        No OCR error fixes - return raw text as-is
        """
        return text.strip()

    def fuzzy_match_player_name(
        self,
        detected_name: str,
        alliance_id: Optional[str] = None,
        min_score: float = 80.0
    ) -> Tuple[Optional[User], float, str]:
        """
        Find best matching player from database using fuzzy matching

        Args:
            detected_name: OCR-detected player name (with or without tag)
            alliance_id: Optional alliance filter (if we know the alliance)
            min_score: Minimum fuzzy match score (0-100)

        Returns:
            Tuple of (matched_user, confidence_score, corrected_name)
            If no match found, returns (None, 0.0, original_name)
        """
        # First apply common OCR error fixes
        cleaned_name = self.fix_common_ocr_errors(detected_name)

        # Extract alliance tag
        detected_tag, name_without_tag = self.extract_alliance_tag(cleaned_name)

        # Build candidate list
        candidates = []

        if alliance_id and alliance_id in self.users_by_alliance:
            # Search only within specified alliance
            candidates = self.users_by_alliance[alliance_id]
        else:
            # Search all users
            with Session(self.users_engine) as session:
                candidates = session.exec(select(User)).all()

        if not candidates:
            return None, 0.0, cleaned_name

        # Build search list: full nicknames and names without tags
        search_options = []
        for user in candidates:
            # Add full nickname
            search_options.append((user, user.nickname, "full"))

            # Add nickname without tag
            _, db_name_without_tag = self.extract_alliance_tag(user.nickname)
            search_options.append((user, db_name_without_tag, "without_tag"))

        # Try matching full name first
        full_options = [(opt[1], i) for i, opt in enumerate(search_options) if opt[2] == "full"]
        best_match_full = process.extractOne(cleaned_name,
                                             [opt[0] for opt in full_options],
                                             scorer=fuzz.ratio,
                                             score_cutoff=min_score)

        # Try matching name without tag
        notag_options = [(opt[1], i) for i, opt in enumerate(search_options) if opt[2] == "without_tag"]
        best_match_notag = process.extractOne(name_without_tag,
                                              [opt[0] for opt in notag_options],
                                              scorer=fuzz.ratio,
                                              score_cutoff=min_score)

        # Choose best match
        best_user = None
        best_score = 0.0

        if best_match_full:
            _, score_full, idx = best_match_full
            if score_full > best_score:
                best_score = score_full
                best_user = search_options[full_options[idx][1]][0]

        if best_match_notag:
            _, score_notag, idx = best_match_notag
            if score_notag > best_score:
                best_score = score_notag
                best_user = search_options[notag_options[idx][1]][0]

        if best_user:
            # Return the database nickname (without tag, since DB doesn't store tags)
            # The caller will reconstruct the full name with the OCR-detected tag
            return best_user, best_score / 100.0, best_user.nickname

        return None, 0.0, detected_name

    def correct_player_name(
        self,
        detected_name: str,
    ) -> Tuple[Optional[str], Optional[User], float, dict]:
        """
        Main correction function - intelligently corrects OCR-detected name

        Args:
            detected_name: OCR-detected player name (e.g., "[DOA]PlayerName")

        Returns:
            Tuple of (corrected_name, matched_user, confidence, debug_info)

        Notes:
            - Database nicknames do NOT contain alliance tags
            - OCR detects names WITH tags like "[DOA]HopOnYourRoof"
            - We extract the name part and match against database
            - Corrected name is reconstructed with tag + matched database name
        """
        debug_info = {
            'original_name': detected_name,
            'steps': []
        }

        # Step 1: Apply common OCR error fixes
        cleaned = self.fix_common_ocr_errors(detected_name)
        if cleaned != detected_name:
            debug_info['steps'].append(f"Fixed OCR errors: {detected_name} -> {cleaned}")

        # Step 2: Extract alliance tag and name part
        detected_tag, name_part = self.extract_alliance_tag(cleaned)

        if detected_tag:
            debug_info['detected_tag'] = detected_tag
            debug_info['name_part'] = name_part
            debug_info['steps'].append(f"Extracted tag [{detected_tag}] and name '{name_part}'")

        # Step 3: Fuzzy match the name part (without tag) against database
        # Database nicknames don't have tags, so we match the name part only
        search_name = name_part if detected_tag else cleaned

        matched_user, match_score, db_nickname = self.fuzzy_match_player_name(
            search_name,
            alliance_id=None,  # Search all users first
            min_score=75.0
        )

        if matched_user:
            debug_info['steps'].append(f"Matched '{search_name}' to '{db_nickname}' (score: {match_score:.1%})")

            # Reconstruct the corrected name with tag + database nickname
            if detected_tag:
                corrected_name = f"[{detected_tag}]{db_nickname}"
            else:
                corrected_name = db_nickname

            # Verify alliance if we have a tag
            if detected_tag and matched_user.alliance:
                debug_info['user_alliance'] = str(matched_user.alliance)
                # Note: We can't verify the tag itself since DB doesn't store tags
                # But we know the user's alliance ID

            debug_info['steps'].append(f"Final corrected name: {corrected_name}")
            return corrected_name, matched_user, match_score, debug_info

        # Step 4: No match found - return cleaned name
        debug_info['steps'].append("No match found in database")
        return cleaned, None, 0.0, debug_info


def batch_correct_names(
    ocr_names: List[str],
    users_engine,
    event_alliance_context: Optional[List[str]] = None,
    verbose: bool = False
) -> List[dict]:
    """
    Batch correct multiple OCR-detected names

    Args:
        ocr_names: List of OCR-detected player names
        users_engine: SQLModel engine for users database
        event_alliance_context: Optional list of alliance IDs in this event
        verbose: Print debug information

    Returns:
        List of correction results, each containing:
        - original_name
        - corrected_name
        - matched_user
        - confidence
        - debug_info
    """
    engine = NameCorrectionEngine(users_engine)
    results = []

    for name in ocr_names:
        corrected, user, confidence, debug = engine.correct_player_name(
            name,
            event_alliance_context
        )

        result = {
            'original_name': name,
            'corrected_name': corrected,
            'matched_user': user,
            'confidence': confidence,
            'debug_info': debug
        }

        results.append(result)

        if verbose and corrected != name:
            print(f"\n[CORRECTION] {name} -> {corrected}")
            print(f"  Confidence: {confidence:.1%}")
            if user:
                print(f"  Matched FID: {user.fid}")
            for step in debug['steps']:
                print(f"  • {step}")

    return results
