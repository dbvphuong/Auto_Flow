import re


WORD_PATTERN = re.compile(r"[^\W_]+(?:[’'.,-][^\W_]+)*", flags=re.UNICODE)
STRONG_BOUNDARY_PATTERN = re.compile(r"[.!?…]|(?:\r\n|\r|\n){2,}")
CLAUSE_BOUNDARY_PATTERN = re.compile(r"[;:,—–]|\r\n|\r|\n")


def count_words(text):
    return len(WORD_PATTERN.findall(text))


def split_text_preserving_content(text, minimum_words, maximum_words):
    """Partition text without changing or discarding any character."""
    if minimum_words < 1 or maximum_words < 1:
        raise ValueError("Số từ tối thiểu và tối đa phải lớn hơn 0.")
    if minimum_words > maximum_words:
        raise ValueError("Số từ tối thiểu không được lớn hơn số từ tối đa.")

    words = list(WORD_PATTERN.finditer(text))
    if not words or len(words) <= maximum_words:
        return [text] if text else []

    chunks = []
    word_start = 0
    character_start = 0
    total_words = len(words)

    while total_words - word_start > maximum_words:
        minimum_end = word_start + minimum_words
        maximum_end = min(word_start + maximum_words, total_words)
        valid_ends = [
            end for end in range(minimum_end, maximum_end + 1)
            if total_words - end == 0 or total_words - end >= minimum_words
        ]
        if not valid_ends:
            # Ví dụ 26 từ với giới hạn 25–25 thì file cuối ngắn là bắt buộc.
            valid_ends = [maximum_end]

        strong_ends = []
        clause_ends = []
        for end in valid_ends:
            boundary_end = words[end].start() if end < total_words else len(text)
            boundary_text = text[words[end - 1].end():boundary_end]
            if STRONG_BOUNDARY_PATTERN.search(boundary_text):
                strong_ends.append(end)
            elif CLAUSE_BOUNDARY_PATTERN.search(boundary_text):
                clause_ends.append(end)

        chosen_end = (
            strong_ends[-1] if strong_ends
            else clause_ends[-1] if clause_ends
            else valid_ends[-1]
        )
        character_end = words[chosen_end].start()
        chunks.append(text[character_start:character_end])
        character_start = character_end
        word_start = chosen_end

    chunks.append(text[character_start:])
    if "".join(chunks) != text:
        raise ValueError("Kiểm tra bảo toàn nội dung thất bại.")
    return chunks


def split_text_into_single_line_chunks(text, minimum_words, maximum_words):
    """Split each source line independently; every returned chunk is one line."""
    chunks = []
    for source_line in text.splitlines():
        if not source_line.strip():
            continue
        line_chunks = split_text_preserving_content(
            source_line, minimum_words, maximum_words
        )
        chunks.extend(chunk for chunk in line_chunks if chunk.strip())
    return chunks


def combine_single_line_chunks(chunks):
    """Combine child-file contents into one source line per child file."""
    chunks = list(chunks)
    if any("\n" in chunk or "\r" in chunk for chunk in chunks):
        raise ValueError("Mỗi nội dung file con phải nằm trên đúng một dòng.")
    return "\n".join(chunks) + ("\n" if chunks else "")
