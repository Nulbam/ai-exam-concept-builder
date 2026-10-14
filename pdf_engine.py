import io
import pymupdf

def parse_page_range(range_str: str, total_pages: int) -> list[int]:
    """
    페이지 범위 문자열(예: '1-5, 8, 10-12' 또는 'all')을 1-based 페이지 번호 리스트로 파싱합니다.
    """
    if not range_str or range_str.strip().lower() in ("all", "전체", ""):
        return list(range(1, total_pages + 1))

    pages = set()
    parts = [p.strip() for p in range_str.split(",") if p.strip()]

    for part in parts:
        if "-" in part:
            sub = part.split("-")
            if len(sub) != 2:
                raise ValueError(f"잘못된 페이지 범위 형식입니다: '{part}'")
            try:
                start, end = int(sub[0].strip()), int(sub[1].strip())
            except ValueError:
                raise ValueError(f"페이지 번호는 숫자여야 합니다: '{part}'")
            if start > end:
                raise ValueError(f"시작 페이지({start})가 끝 페이지({end})보다 클 수 없습니다.")
            if start < 1 or end > total_pages:
                raise ValueError(f"페이지 번호({start}-{end})는 1부터 {total_pages} 사이여야 합니다.")
            for p in range(start, end + 1):
                pages.add(p)
        else:
            try:
                p = int(part)
            except ValueError:
                raise ValueError(f"페이지 번호는 숫자여야 합니다: '{part}'")
            if p < 1 or p > total_pages:
                raise ValueError(f"페이지 번호({p})는 1부터 {total_pages} 사이여야 합니다.")
            pages.add(p)

    result = sorted(list(pages))
    if not result:
        raise ValueError("선택된 페이지가 없습니다.")
    return result


def inspect_pdf(pdf_bytes: bytes, filename: str) -> dict:
    """
    업로드된 PDF의 메모리 바이트를 분석하여 전체 페이지 수,
    텍스트/스캔형 페이지 여부, 권장 범위 및 경고를 반환합니다.
    """
    file_size_mb = round(len(pdf_bytes) / (1024 * 1024), 2)
    warnings = []

    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        raise ValueError(f"PDF 파일을 열 수 없습니다. 손상된 파일인지 확인해 주세요. (원인: {str(e)})")

    if doc.is_encrypted:
        doc.close()
        raise ValueError("암호가 걸려있는 PDF 파일은 지원하지 않습니다. 암호를 해제 후 다시 업로드해 주세요.")

    total_pages = len(doc)
    if total_pages == 0:
        doc.close()
        raise ValueError("PDF 문서에 페이지가 존재하지 않습니다.")

    text_pages_count = 0
    scanned_pages_count = 0

    # 각 페이지 진단 (텍스트 길이 기준: 50자 미만이면 스캔형으로 분류)
    for page_idx in range(total_pages):
        page = doc[page_idx]
        text = page.get_text("text").strip()
        if len(text) >= 50:
            text_pages_count += 1
        else:
            scanned_pages_count += 1

    doc.close()

    # 권장 페이지 범위 산출
    if total_pages <= 15:
        recommended_range = f"1-{total_pages} (전체)"
    else:
        recommended_range = "1-10 (효율적인 시험 정리를 위해 10~15페이지 내외 권장)"

    if scanned_pages_count > 0:
        warnings.append(
            f"전체 {total_pages}페이지 중 {scanned_pages_count}페이지가 스캔본(이미지 위주)으로 감지되었습니다. "
            "해당 페이지는 비전 AI 모드로 자동 전환되어 분석됩니다."
        )

    if total_pages > 40:
        warnings.append("전체 페이지가 다소 많습니다. 특정 시험 범위(예: 1-15)를 지정하시면 더 빠르고 정밀한 요약이 가능합니다.")

    return {
        "filename": filename,
        "file_size_mb": file_size_mb,
        "total_pages": total_pages,
        "text_pages_count": text_pages_count,
        "scanned_pages_count": scanned_pages_count,
        "recommended_range": recommended_range,
        "warnings": warnings
    }


def extract_pdf_pages_content(pdf_bytes: bytes, page_numbers: list[int], handwriting_mode: bool) -> list[dict]:
    """
    선택된 페이지 번호(1-based)를 대상으로 내용을 추출합니다.
    - handwriting_mode가 켜져 있거나 텍스트가 부족한 스캔 페이지는 고해상도 PNG 바이트로 렌더링
    - 일반 텍스트 모드이고 텍스트가 충분한 페이지는 텍스트 문자열로 추출
    """
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    pages_content = []

    for p_num in page_numbers:
        # PyMuPDF는 0-based 인덱스
        page_idx = p_num - 1
        if page_idx < 0 or page_idx >= len(doc):
            continue

        page = doc[page_idx]
        raw_text = page.get_text("text").strip()

        # 필기 추출 모드이거나 텍스트가 거의 없는 스캔 페이지인 경우 이미지 렌더링
        is_scanned = len(raw_text) < 40
        if handwriting_mode or is_scanned:
            # 고해상도 렌더링 (150 DPI는 AI 비전에 적합한 밸런스)
            pix = page.get_pixmap(dpi=150)
            img_bytes = pix.tobytes("png")
            pages_content.append({
                "page_num": p_num,
                "type": "image",
                "image_bytes": img_bytes,
                "mime_type": "image/png",
                "text_snippet": raw_text[:100] if raw_text else "(시각 이미지 데이터)"
            })
        else:
            pages_content.append({
                "page_num": p_num,
                "type": "text",
                "text": raw_text
            })

    doc.close()
    return pages_content
