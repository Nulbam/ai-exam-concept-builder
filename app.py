import os
import time
import json
import re
import logging
from flask import Flask, jsonify, render_template, request
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pdf_engine import inspect_pdf, parse_page_range, extract_pdf_pages_content

# .env 환경변수 로드
load_dotenv()

# 로깅 설정 (print() 대신 logging 사용)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("ExamConceptBuilder")

app = Flask(__name__)

# 업로드 용량 제한 설정 (기본값 20MB)
max_mb = int(os.getenv("MAX_UPLOAD_MB", "20"))
app.config["MAX_CONTENT_LENGTH"] = max_mb * 1024 * 1024

@app.errorhandler(413)
def request_entity_too_large(error):
    logger.error("[ERROR] 파일 크기 제한 초과 (MAX_UPLOAD_MB)")
    return jsonify({
        "status": "error",
        "message": f"파일 용량이 너무 큽니다. 최대 {max_mb}MB까지 업로드 가능합니다."
    }), 413

@app.route("/", methods=["GET"])
def index():
    """메인 화면"""
    try:
        return render_template("index.html")
    except Exception:
        return "AI Exam Concept Builder 서버가 정상 실행 중입니다. (index.html은 Step 17에서 구성됩니다.)", 200

@app.route("/health", methods=["GET"])
def health():
    """서버 상태 및 API 키 설정 여부, 활성 모델 확인 (키 값 자체는 노출 금지)"""
    load_dotenv(override=True)
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    is_key_configured = bool(api_key and api_key != "your_gemini_api_key_here")
    active_model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

    logger.info(f"[REQUEST] GET /health - model={active_model} key_configured={is_key_configured}")

    return jsonify({
        "status": "ok",
        "api_key_configured": is_key_configured,
        "active_model": active_model
    }), 200

@app.route("/inspect", methods=["POST"])
def inspect():
    """PDF 업로드 진단 엔드포인트"""
    logger.info("[REQUEST] POST /inspect")

    if "file" not in request.files:
        logger.warning("[ERROR] 파일 미선택")
        return jsonify({"status": "error", "message": "업로드할 PDF 파일을 선택해 주세요."}), 400

    file = request.files["file"]
    if not file or not file.filename:
        logger.warning("[ERROR] 파일 미선택")
        return jsonify({"status": "error", "message": "업로드할 PDF 파일을 선택해 주세요."}), 400

    filename = file.filename
    if not filename.lower().endswith(".pdf"):
        logger.warning(f"[ERROR] PDF 파일이 아님: {filename}")
        return jsonify({"status": "error", "message": "PDF 형식(.pdf)의 파일만 업로드할 수 있습니다."}), 400

    try:
        pdf_bytes = file.read()
        if not pdf_bytes:
            return jsonify({"status": "error", "message": "비어 있는 파일(0 바이트)입니다."}), 400

        diag = inspect_pdf(pdf_bytes, filename)
        logger.info(f"[FILE] name={diag['filename']} size={diag['file_size_mb']}MB pages={diag['total_pages']}")

        return jsonify({
            "status": "success",
            "data": diag
        }), 200

    except ValueError as ve:
        logger.error(f"[ERROR] PDF 진단 오류: {str(ve)}")
        return jsonify({"status": "error", "message": str(ve)}), 400
    except Exception as e:
        logger.error(f"[ERROR] 서버 내부 오류: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": "PDF 파일을 처리하는 중 오류가 발생했습니다."}), 500

def get_gemini_client():
    """Gemini 클라이언트를 생성하고 키 설정을 검증합니다."""
    load_dotenv(override=True)
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or api_key == "your_gemini_api_key_here":
        raise ValueError("Gemini API Key가 설정되지 않았습니다. .env 파일에 GEMINI_API_KEY를 올바르게 입력해 주세요.")
    return genai.Client(api_key=api_key)

def call_gemini_with_fallback(client, contents, config=None):
    """
    주 모델 호출 후 503(과부하) 또는 429 발생 시 다른 안정 모델로 자동 폴백 및 재시도합니다.
    """
    primary_model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
    # 폴백 후보 목록
    fallback_candidates = [primary_model]
    for alt in ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]:
        if alt not in fallback_candidates:
            fallback_candidates.append(alt)

    last_error = None
    for idx, model_name in enumerate(fallback_candidates):
        try:
            logger.info(f"[AI] Gemini API 호출 (모델: {model_name})")
            if config:
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config
                )
            else:
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents
                )
            logger.info(f"[AI] Gemini API 응답 완료 (성공 모델: {model_name})")
            return response, model_name
        except Exception as e:
            err_str = str(e)
            last_error = e
            if "503" in err_str or "UNAVAILABLE" in err_str or "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                if idx < len(fallback_candidates) - 1:
                    next_model = fallback_candidates[idx + 1]
                    logger.warning(f"[AI] 모델 {model_name} 일시적 과부하(503/429) 감지. 대체 모델({next_model})로 즉시 자동 전환합니다...")
                    time.sleep(1)
                    continue
            raise e

    raise last_error

SUMMARIZE_SYSTEM_PROMPT = """당신은 대학 시험 대비 전문 1타 튜터입니다.

[절대 규칙 - 폐쇄형 컨텍스트(Closed Domain)]
1. 반드시 제공된 교재(PDF) 내용에만 기반하여 작성해야 합니다. 외부 지식을 임의로 지어내거나 추측하지 마십시오.
2. 교재에 없는 내용은 절대 생성하지 말고, 필요 시 "교재에 명시되지 않음"이라고 명확히 표기하십시오.
3. 교재에 명시된 학술 용어와 공식, 정의를 왜곡 없이 그대로 사용하십시오.
4. 각 개념마다 반드시 [출처: p.X] 형태로 원본 페이지 번호를 명시하십시오.

[전체 페이지 누락 방지 규칙 - 매우 중요]
- 사용자가 선택한 모든 페이지(앞부분부터 끝부분까지)의 핵심 내용을 빠짐없이 균등하게 요약해야 합니다.
- 절대 앞부분만 길게 쓰고 뒷부분은 페이지만 나열하거나 생략 기호(...)로 때우지 마십시오.
- 모든 [출처: p.X] 표기에는 반드시 해당 페이지의 핵심 정의, 원리, 주요 내용이 충분하고 알기 쉽게 설명되어 있어야 합니다.

[시각적 컬러 강조 서식 규칙 - 매우 중요 (가독성 극대화)]
시험 직전 한눈에 들어오는 직관적인 단권화 노트를 만들기 위해, 본문 및 표(Table) 내부에서 아래 서식을 적극 사용하십시오:
1. 가장 중요한 핵심 개념 및 시험 빈출 정답: `<red>핵심 키워드</red>` 태그로 감싸십시오. (빨간색 굵은 글씨로 렌더링됩니다)
2. 반드시 암기해야 하는 보충 키워드 및 전문 용어: `<blue>암기 키워드</blue>` 태그로 감싸십시오. (파란색 강조 글씨로 렌더링됩니다)
3. 핵심 문장이나 형광펜 마킹 영역: `<mark>핵심 내용</mark>` 태그로 감싸십시오. (노란색 형광펜 하이라이트로 렌더링됩니다)
4. 교수님/강사의 특별 강조 및 주의 필기:
   `> 🚨 **[필기 강조] 핵심 내용...**`
   형태로 작성하십시오. (빨간 점선 테두리의 특별 강조 카드 박스로 렌더링됩니다)

[핵심 표(Table) 정리 규칙]
- 외우기 어렵거나 비교·분류가 필요한 개념(예: 항목별 비교, 직급/역할 분류, 조건별 차이점 등)은 줄글로만 쓰지 말고, 반드시 가독성 높은 **마크다운 표(`| 구분 | 대상 A | 대상 B | 시험 출제 포인트 |`)**를 생성하여 한눈에 들어오게 정리하십시오.
- 표의 각 셀 내부에서도 중요한 부분은 `<red>...</red>`, `<blue>...</blue>`를 자유롭게 사용하여 시각적 대조를 극대화하십시오.

[필기 모드 규칙]
필기 모드가 활성화되어 있거나 페이지 이미지에 손글씨/마킹이 있는 경우:
- 빨간색(중요), 파란색(보충), 형광펜(핵심 키워드)으로 필기된 내용을 최우선으로 인식하여 '2. 교수님/강사 필기 포인트' 항목에 별도 강조 블록으로 분리해 표기하십시오.

[출력 형식 - 반드시 아래 마크다운 구조를 정확히 지킬 것]
# 📌 핵심 단권화 요약
> 📌 본 요약본은 교재 및 강의 자료 내 빨간색 펜, 파란색 펜, 형광펜으로 강조된 핵심 필기 사항을 중심으로 재구성되었습니다.

## 1. 필수 암기 핵심 개념
(선택된 전 페이지에 걸친 핵심 정의와 분류 체계 정리. 중요 키워드는 <red>, <blue>, <mark> 태그 적용)

## 2. 교수님/강사 필기 포인트 (유색 필기 및 강조 사항)
(선택된 모든 페이지의 빨강/파랑/형광펜 마킹 영역 정리. 필기 없을 시 "특이 필기 사항 없음")

## 3. 시험 빈출 예상 공식 및 키워드
(주요 공식, 필수 암기 단어 목록)

## 4. 헷갈리기 쉬운 비교/주의점 (암기 표 필수 첨부)
(비교 가능한 개념들은 반드시 마크다운 표 형태 `| 구분 | 대상 A | 대상 B | 시험 출제 포인트 |` 로 한눈에 대조할 수 있게 첨부하고, 표 안에도 <red>, <blue> 적용)
"""

@app.route("/summarize", methods=["POST"])
def summarize():
    """선택된 페이지 및 필기 모드를 반영하여 Gemini 개념 요약을 생성합니다."""
    logger.info("[REQUEST] POST /summarize")

    if "file" not in request.files:
        logger.warning("[ERROR] 파일 미선택")
        return jsonify({"status": "error", "message": "PDF 파일을 업로드해 주세요."}), 400

    file = request.files["file"]
    if not file or not file.filename or not file.filename.lower().endswith(".pdf"):
        logger.warning("[ERROR] 올바르지 않은 파일")
        return jsonify({"status": "error", "message": "유효한 PDF 파일을 선택해 주세요."}), 400

    pages_input = request.form.get("pages", "all").strip()
    handwriting_filter = request.form.get("handwriting_mode", "false").lower() in ("true", "1", "yes")

    logger.info(f"[MODE] pages={pages_input} handwriting_filter={handwriting_filter}")

    try:
        pdf_bytes = file.read()
        if not pdf_bytes:
            return jsonify({"status": "error", "message": "비어 있는 PDF 파일입니다."}), 400

        # PDF 진단 및 대상 페이지 슬라이싱
        diag = inspect_pdf(pdf_bytes, file.filename)
        total_pages = diag["total_pages"]
        page_numbers = parse_page_range(pages_input, total_pages)

        # 내용 추출 (일반 텍스트 또는 비전 이미지)
        pages_content = extract_pdf_pages_content(pdf_bytes, page_numbers, handwriting_filter)

        # Gemini 멀티모달 입력 프롬프트 구성
        contents = [SUMMARIZE_SYSTEM_PROMPT]
        for item in pages_content:
            p_num = item["page_num"]
            if item["type"] == "text":
                contents.append(f"\n--- [교재 {p_num}페이지 텍스트] ---\n{item['text']}\n")
            elif item["type"] == "image":
                contents.append(f"\n--- [교재 {p_num}페이지 시각 이미지] ---\n")
                contents.append(types.Part.from_bytes(data=item["image_bytes"], mime_type=item["mime_type"]))

        # Gemini API 호출 (자동 폴백 포함)
        client = get_gemini_client()
        response, used_model = call_gemini_with_fallback(
            client=client,
            contents=contents,
            config=types.GenerateContentConfig(
                max_output_tokens=8192
            )
        )

        summary_text = response.text if response.text else "요약 결과가 비어 있습니다."

        return jsonify({
            "status": "success",
            "summary": summary_text,
            "pages": page_numbers,
            "model": used_model
        }), 200

    except ValueError as ve:
        logger.error(f"[ERROR] 요청 처리 오류: {str(ve)}")
        return jsonify({"status": "error", "message": str(ve)}), 400
    except Exception as e:
        err_msg = str(e)
        logger.error(f"[ERROR] Gemini 요약 생성 실패: {err_msg}", exc_info=True)
        if "API_KEY_INVALID" in err_msg or "401" in err_msg or "403" in err_msg:
            return jsonify({"status": "error", "message": "Gemini API 인증에 실패했습니다. 올바른 API 키인지 확인해 주세요."}), 401
        elif "RESOURCE_EXHAUSTED" in err_msg or "429" in err_msg:
            return jsonify({"status": "error", "message": "Gemini API 호출 한도(Quota)를 초과했습니다. 잠시 후 다시 시도해 주세요."}), 429
        elif "503" in err_msg or "UNAVAILABLE" in err_msg:
            return jsonify({"status": "error", "message": "Google Gemini 서버의 일시적인 트래픽 과부하(503) 상태입니다. 잠시 후(약 10~20초 뒤) 다시 시도해 주세요."}), 503
        return jsonify({"status": "error", "message": f"요약 생성 중 오류가 발생했습니다: {err_msg}"}), 500

QUIZ_SYSTEM_PROMPT = """당신은 대학 시험 출제 위원입니다.
제공된 시험 요약 노트 내용만을 바탕으로 학생들의 실전 이해도를 점검할 5문항 내외의 퀴즈를 출제하십시오.

[절대 규칙 - 폐쇄형 컨텍스트]
1. 반드시 제공된 시험 요약 내용에 포함된 팩트에만 기반하여 출제하십시오. 외부 지식이나 교재에 없는 내용을 절대 묻지 마십시오.
2. 5문항 내외(객관식 3~4문항, 단답형 1~2문항)로 구성하십시오.
3. 객관식(multiple_choice)은 4지선다(보기 4개)로 매력적인 오답과 1개의 명확한 정답을 포함하십시오.
4. 단답형(short_answer)은 핵심 개념 키워드나 괄호/빈칸 채우기 형태로 작성하십시오.
5. 모든 문항마다 반드시 정답(answer), 명쾌한 해설(explanation), 그리고 요약본에 기재된 출처 페이지(source_page, 숫자)를 반드시 기재하십시오.
6. 응답은 반드시 아래 JSON 스키마를 만족하는 순수 JSON 객체여야 합니다.

JSON 형식:
{
  "quizzes": [
    {
      "id": 1,
      "type": "multiple_choice",
      "question": "문제 내용",
      "options": ["보기1", "보기2", "보기3", "보기4"],
      "answer": "보기1",
      "explanation": "해설 및 근거",
      "source_page": 3
    },
    {
      "id": 2,
      "type": "short_answer",
      "question": "문제 내용 (빈칸 채우기 등)",
      "answer": "정답 키워드",
      "explanation": "해설",
      "source_page": 5
    }
  ]
}
"""

@app.route("/quiz", methods=["POST"])
def quiz():
    """요약된 개념을 바탕으로 객관식/단답형 퀴즈 JSON을 생성합니다."""
    logger.info("[REQUEST] POST /quiz")

    data = request.get_json(silent=True) or request.form
    summary_text = data.get("summary", "").strip()

    if not summary_text:
        logger.warning("[ERROR] 요약 내용 누락")
        return jsonify({
            "status": "error",
            "message": "퀴즈를 출제할 요약 내용이 없습니다. 먼저 개념 요약을 완료해 주세요."
        }), 400

    try:
        client = get_gemini_client()
        response, used_model = call_gemini_with_fallback(
            client=client,
            contents=[QUIZ_SYSTEM_PROMPT, f"\n[시험 대비 요약본 내용]\n{summary_text}"],
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )

        raw_json = response.text.strip() if response.text else "{}"
        if raw_json.startswith("```"):
            raw_json = re.sub(r"^```(?:json)?\s*", "", raw_json)
            raw_json = re.sub(r"\s*```$", "", raw_json)

        try:
            quiz_data = json.loads(raw_json)
        except json.JSONDecodeError as jde:
            logger.error(f"[ERROR] JSON 파싱 실패: {str(jde)} - Raw: {raw_json[:200]}")
            return jsonify({
                "status": "error",
                "message": "AI가 생성한 퀴즈 데이터를 JSON으로 변환하지 못했습니다. 다시 시도해 주세요."
            }), 500

        quizzes = quiz_data.get("quizzes", [])
        logger.info(f"[AI] 퀴즈 생성 완료 ({len(quizzes)}문항)")

        return jsonify({
            "status": "success",
            "quizzes": quizzes
        }), 200

    except ValueError as ve:
        logger.error(f"[ERROR] 요청 처리 오류: {str(ve)}")
        return jsonify({"status": "error", "message": str(ve)}), 400
    except Exception as e:
        err_msg = str(e)
        logger.error(f"[ERROR] 퀴즈 생성 실패: {err_msg}", exc_info=True)
        if "API_KEY_INVALID" in err_msg or "401" in err_msg or "403" in err_msg:
            return jsonify({"status": "error", "message": "Gemini API 인증에 실패했습니다. 올바른 API 키인지 확인해 주세요."}), 401
        elif "RESOURCE_EXHAUSTED" in err_msg or "429" in err_msg:
            return jsonify({"status": "error", "message": "Gemini API 호출 한도(Quota)를 초과했습니다. 잠시 후 다시 시도해 주세요."}), 429
        elif "503" in err_msg or "UNAVAILABLE" in err_msg:
            return jsonify({"status": "error", "message": "Google Gemini 서버의 일시적인 트래픽 과부하(503) 상태입니다. 잠시 후(약 10~20초 뒤) 다시 시도해 주세요."}), 503
        return jsonify({"status": "error", "message": f"퀴즈 생성 중 오류가 발생했습니다: {err_msg}"}), 500

ASK_SYSTEM_PROMPT = """당신은 학생들의 질문에 답해주는 친절하고 명쾌한 시험 대비 전문 튜터입니다.

[절대 규칙 - 폐쇄형 컨텍스트(Closed Domain)]
1. 오직 제공된 [교재 및 요약 내용]의 팩트에만 기반하여 답변하십시오. 외부 지식을 임의로 지어내거나 일반적인 상식으로 추측하여 답하지 마십시오.
2. 제공된 내용에 적혀 있지 않거나 확인할 수 없는 질문에 대해서는 반드시 "현재 업로드된 교재 내용에서는 확인할 수 없습니다."라고 정직하고 명확하게 답하십시오.
3. 답변의 끝에는 항상 답변의 근거가 된 교재 페이지를 기재하십시오.
   예시: [근거: 교재 p.X]
4. 친절하고 명확한 튜터 어조로 서술하십시오.
"""

@app.route("/ask", methods=["POST"])
def ask():
    """정리된 개념 및 교재 범위를 바탕으로 사용자 질문에 답변합니다."""
    logger.info("[REQUEST] POST /ask")

    data = request.get_json(silent=True) or request.form
    question = data.get("question", "").strip()
    context = data.get("context", "").strip()

    if not question:
        logger.warning("[ERROR] 질문 내용 누락")
        return jsonify({"status": "error", "message": "질문 내용을 입력해 주세요."}), 400

    if not context:
        logger.warning("[ERROR] 참조 문맥 누락")
        return jsonify({
            "status": "error",
            "message": "참조할 교재 요약 내용이 없습니다. 먼저 개념 요약을 진행해 주세요."
        }), 400

    try:
        client = get_gemini_client()
        response, used_model = call_gemini_with_fallback(
            client=client,
            contents=[
                ASK_SYSTEM_PROMPT,
                f"[참조 교재 및 요약 내용]\n{context}\n",
                f"[학생의 질문]\n{question}"
            ]
        )

        answer_text = response.text if response.text else "답변을 생성하지 못했습니다."

        return jsonify({
            "status": "success",
            "answer": answer_text
        }), 200

    except ValueError as ve:
        logger.error(f"[ERROR] 요청 처리 오류: {str(ve)}")
        return jsonify({"status": "error", "message": str(ve)}), 400
    except Exception as e:
        err_msg = str(e)
        logger.error(f"[ERROR] 질의응답 처리 실패: {err_msg}", exc_info=True)
        if "API_KEY_INVALID" in err_msg or "401" in err_msg or "403" in err_msg:
            return jsonify({"status": "error", "message": "Gemini API 인증에 실패했습니다. 올바른 API 키인지 확인해 주세요."}), 401
        elif "RESOURCE_EXHAUSTED" in err_msg or "429" in err_msg:
            return jsonify({"status": "error", "message": "Gemini API 호출 한도(Quota)를 초과했습니다. 잠시 후 다시 시도해 주세요."}), 429
        elif "503" in err_msg or "UNAVAILABLE" in err_msg:
            return jsonify({"status": "error", "message": "Google Gemini 서버의 일시적인 트래픽 과부하(503) 상태입니다. 잠시 후(약 10~20초 뒤) 다시 시도해 주세요."}), 503
        return jsonify({"status": "error", "message": f"질의응답 중 오류가 발생했습니다: {err_msg}"}), 500

if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "true").lower() in ("true", "1", "yes")
    logger.info(f"서버를 시작합니다. (Debug={debug_mode})")
    app.run(host="127.0.0.1", port=5000, debug=debug_mode)
