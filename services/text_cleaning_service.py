"""Text cleaning service: strip HTML scraping artifacts from documentation files."""

import re


def clean_document_text(raw: str) -> str:
    """Clean scraped documentation text by removing duplicate patterns and artifacts.

    Fixes:
    - Bullet lines (  - text) followed by duplicate bare paragraph of same text
    - "Note | ..." / "Important | ..." table artifacts with 3x repetition
    - Excessive whitespace padding from HTML indentation
    - 3+ consecutive newlines collapsed to 2
    """
    lines = raw.split("\n")
    cleaned = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # --- Pattern 1: Note/Important/Warning table artifacts ---
        # "Note | text" followed by the same text bare, then "Note" header, then same text again
        callout_match = re.match(r'^(Note|Important|Warning|Tip)\s*\|\s*(.+)', line.strip())
        if callout_match:
            callout_type = callout_match.group(1)
            callout_text_lines = [callout_match.group(2).strip()]

            # Collect continuation lines of the callout (indented text)
            j = i + 1
            while j < len(lines) and lines[j].strip() and not re.match(r'^(Note|Important|Warning|Tip)\s*$', lines[j].strip()):
                next_stripped = lines[j].strip()
                # Stop if we hit another pattern or heading
                if re.match(r'^#{2,}', next_stripped) or re.match(r'^\s{0,2}-\s', lines[j]):
                    break
                # Check if this is a duplicate bare repeat of the callout text
                if next_stripped == callout_text_lines[0]:
                    j += 1
                    continue
                callout_text_lines.append(next_stripped)
                j += 1

            # Skip the bare "Note" / "Important" header line and its repeated block
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and re.match(r'^(Note|Important|Warning|Tip)\s*$', lines[j].strip()):
                j += 1  # skip bare header
                # Skip the 3rd repetition of the text
                while j < len(lines) and lines[j].strip():
                    j += 1

            # Emit clean callout
            full_callout = " ".join(callout_text_lines)
            full_callout = re.sub(r'\s+', ' ', full_callout).strip()
            cleaned.append(f"**{callout_type}:** {full_callout}")
            cleaned.append("")
            i = j
            continue

        # --- Pattern 1b: Standalone "Note" / "Important" header followed by repeated text ---
        standalone_callout = re.match(r'^(Note|Important|Warning|Tip)\s*$', line.strip())
        if standalone_callout:
            # Skip this and its content block (already captured above)
            j = i + 1
            while j < len(lines) and lines[j].strip():
                j += 1
            i = j
            continue

        # --- Pattern 2: Bullet line followed by duplicate bare paragraph ---
        bullet_match = re.match(r'^(\s{0,4}-\s)(.+)', line)
        if bullet_match:
            bullet_text = bullet_match.group(2).strip()
            # Collect full bullet text (may span multiple indented lines)
            bullet_lines = [bullet_text]
            j = i + 1
            while j < len(lines):
                next_line = lines[j]
                # Continuation of bullet: indented, not a new bullet, not empty
                if next_line.strip() and not re.match(r'^\s{0,4}-\s', next_line) and not re.match(r'^#{2,}', next_line.strip()):
                    bullet_lines.append(next_line.strip())
                    j += 1
                else:
                    break

            full_bullet = " ".join(bullet_lines)
            full_bullet = re.sub(r'\s+', ' ', full_bullet).strip()

            # Check if next non-empty lines duplicate the bullet content
            k = j
            while k < len(lines) and not lines[k].strip():
                k += 1

            # Build the potential duplicate paragraph
            dup_lines = []
            m = k
            while m < len(lines) and lines[m].strip():
                dup_lines.append(lines[m].strip())
                m += 1

            dup_text = " ".join(dup_lines)
            dup_text = re.sub(r'\s+', ' ', dup_text).strip()

            if dup_text and _texts_match(full_bullet, dup_text):
                # Skip the duplicate paragraph
                cleaned.append(f"- {full_bullet}")
                i = m
            else:
                cleaned.append(f"- {full_bullet}")
                i = j
            continue

        # --- Pattern 3: Clean up excessive indentation ---
        stripped = line.strip()
        if stripped:
            # Preserve heading markers
            heading_match = re.match(r'^(#{1,6}\s)', stripped)
            if heading_match:
                cleaned.append(stripped)
            else:
                cleaned.append(stripped)
        else:
            cleaned.append("")
        i += 1

    result = "\n".join(cleaned)

    # Collapse 3+ consecutive newlines to 2
    result = re.sub(r'\n{3,}', '\n\n', result)

    return result.strip()


def _texts_match(a: str, b: str) -> bool:
    """Check if two texts are essentially the same (ignoring whitespace differences)."""
    norm_a = re.sub(r'\s+', ' ', a).strip().lower()
    norm_b = re.sub(r'\s+', ' ', b).strip().lower()
    if norm_a == norm_b:
        return True
    # Partial match: the duplicate sometimes starts with part of the bullet
    if len(norm_a) > 20 and len(norm_b) > 20:
        # Check if one starts with the other (for truncated duplicates)
        if norm_a.startswith(norm_b[:40]) or norm_b.startswith(norm_a[:40]):
            return True
    return False
