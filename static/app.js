/**
 * AI Exam Concept Builder - Frontend Application Logic
 * Vanilla JavaScript (No Frameworks)
 */

document.addEventListener("DOMContentLoaded", () => {
    console.log("[PAGE] 페이지 로드 완료");

    // 앱 상태 저장소
    const state = {
        selectedFile: null,
        diagnosticData: null,
        summaryText: "",
        quizzes: []
    };

    // DOM 요소 캐싱
    const elements = {
        // 헤더 상태
        statusDot: document.getElementById("statusDot"),
        statusText: document.getElementById("statusText"),
        activeModelTag: document.getElementById("activeModelTag"),

        // 업로드 & 진단
        dropZone: document.getElementById("dropZone"),
        fileInput: document.getElementById("pdfFileInput"),
        diagnosticCard: document.getElementById("diagnosticCard"),
        diagFilename: document.getElementById("diagFilename"),
        diagMeta: document.getElementById("diagMeta"),
        diagTotalPages: document.getElementById("diagTotalPages"),
        diagTextPages: document.getElementById("diagTextPages"),
        diagScannedPages: document.getElementById("diagScannedPages"),
        diagWarningsBox: document.getElementById("diagWarningsBox"),
        diagWarningsList: document.getElementById("diagWarningsList"),
        pageRangeInput: document.getElementById("pageRangeInput"),
        recommendedRangeHint: document.getElementById("recommendedRangeHint"),
        handwritingModeToggle: document.getElementById("handwritingModeToggle"),
        btnSummarize: document.getElementById("btnSummarize"),

        // 요약 섹션
        summarySection: document.getElementById("summarySection"),
        summaryContent: document.getElementById("summaryContent"),
        summaryModelBadge: document.getElementById("summaryModelBadge"),
        btnPrintPdf: document.getElementById("btnPrintPdf"),
        btnGenerateQuiz: document.getElementById("btnGenerateQuiz"),
        printDocMeta: document.getElementById("printDocMeta"),

        // 퀴즈 섹션
        quizSection: document.getElementById("quizSection"),
        quizForm: document.getElementById("quizForm"),
        quizList: document.getElementById("quizList"),
        quizScoreBadge: document.getElementById("quizScoreBadge"),
        quizScoreText: document.getElementById("quizScoreText"),

        // Q&A 섹션
        qaSection: document.getElementById("qaSection"),
        chatBox: document.getElementById("chatBox"),
        chatForm: document.getElementById("chatForm"),
        chatInput: document.getElementById("chatInput"),

        // 로딩 및 토스트
        loadingOverlay: document.getElementById("loadingOverlay"),
        loadingMessage: document.getElementById("loadingMessage"),
        toastContainer: document.getElementById("toastContainer")
    };

    // 1. 서버 헬스 체크
    async function checkServerHealth() {
        try {
            const res = await fetch("/health");
            const data = await res.json();
            if (data.status === "ok") {
                elements.statusDot.className = "status-dot active";
                elements.activeModelTag.textContent = `모델: ${data.active_model}`;
                if (data.api_key_configured) {
                    elements.statusText.textContent = "서버 및 API 정상";
                } else {
                    elements.statusDot.className = "status-dot warning";
                    elements.statusText.textContent = "API 키 미설정 (.env 확인 필요)";
                    showToast("Gemini API 키가 아직 설정되지 않았습니다. .env 파일에 키를 입력해 주세요.", "warning");
                }
            }
        } catch (err) {
            console.error("[ERROR] 헬스체크 실패", err);
            elements.statusDot.className = "status-dot";
            elements.statusText.textContent = "서버 연결 불가";
            showToast("백엔드 서버와 통신할 수 없습니다.", "error");
        }

        // Vercel 환경일 때 최대 용량 텍스트 자동 갱신
        if (window.location.hostname.includes("vercel.app")) {
            const limitEl = document.getElementById("dropLimitText");
            if (limitEl) {
                limitEl.textContent = "Vercel 무료 호스팅 1회 한도: 최대 4.5MB (.pdf 지원)";
            }
        }
    }

    // 2. 파일 선택 및 드래그 앤 드롭
    elements.dropZone.addEventListener("click", () => elements.fileInput.click());

    elements.dropZone.addEventListener("dragover", (e) => {
        e.preventDefault();
        elements.dropZone.classList.add("dragover");
    });

    elements.dropZone.addEventListener("dragleave", () => {
        elements.dropZone.classList.remove("dragover");
    });

    elements.dropZone.addEventListener("drop", (e) => {
        e.preventDefault();
        elements.dropZone.classList.remove("dragover");
        if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            handleFileSelect(e.dataTransfer.files[0]);
        }
    });

    elements.fileInput.addEventListener("change", (e) => {
        if (e.target.files && e.target.files.length > 0) {
            handleFileSelect(e.target.files[0]);
        }
    });

    // 안전한 JSON 파싱 및 HTTP 상태코드별 친절한 에러 핸들러
    async function parseResponseJson(res) {
        if (res.status === 413) {
            throw new Error("파일 용량이 너무 큽니다 (Request Entity Too Large). Vercel 호스팅 환경에서는 파일당 최대 4.5MB까지 업로드 가능합니다. (로컬 PC 서버에서는 20MB 지원)");
        }
        if (res.status === 504) {
            throw new Error("서버 처리 시간(Timeout)이 초과되었습니다. 대상 페이지 범위를 5~10페이지 내외로 좁혀서 다시 시도해 주세요.");
        }
        const text = await res.text();
        try {
            return JSON.parse(text);
        } catch (e) {
            console.error("[ERROR] JSON 파싱 불가 원문 응답:", text);
            throw new Error(text || "서버에서 올바른 응답을 받지 못했습니다.");
        }
    }

    function handleFileSelect(file) {
        if (!file.name.toLowerCase().endsWith(".pdf")) {
            console.error("[ERROR] 지원하지 않는 파일 형식:", file.name);
            showToast("PDF(.pdf) 형식의 파일만 업로드할 수 있습니다.", "error");
            return;
        }

        const sizeMb = parseFloat((file.size / (1024 * 1024)).toFixed(2));
        console.log(`[UPLOAD] 파일 선택: ${file.name} (${sizeMb}MB)`);

        // Vercel 무료 호스팅 환경(도메인 vercel.app)은 1회 요청 크기가 4.5MB로 제한됨
        const isVercel = window.location.hostname.includes("vercel.app");
        const maxLimitMb = isVercel ? 4.5 : 20.0;

        if (sizeMb > maxLimitMb) {
            console.error(`[ERROR] 파일 크기 제한 초과 (${sizeMb}MB > ${maxLimitMb}MB)`);
            if (isVercel) {
                showToast(`선택하신 PDF(${sizeMb}MB)가 Vercel 무료 호스팅 1회 업로드 한도(4.5MB)를 초과했습니다. PDF 페이지를 분할하거나 압축하여 업로드해 주세요. (로컬 PC에서는 20MB까지 처리 가능합니다)`, "error");
            } else {
                showToast(`파일 용량이 너무 큽니다 (${sizeMb}MB). 최대 ${maxLimitMb}MB 이하의 파일만 업로드할 수 있습니다.`, "error");
            }
            return;
        }

        state.selectedFile = file;
        inspectPdf(file);
    }

    // 3. PDF 진단 API 호출
    async function inspectPdf(file) {
        showLoading("PDF 문서 진단 중...");
        console.log(`[INSPECT] PDF 진단 시작: ${file.name}`);

        const formData = new FormData();
        formData.append("file", file);

        try {
            const res = await fetch("/inspect", {
                method: "POST",
                body: formData
            });

            const result = await parseResponseJson(res);
            if (!res.ok || result.status !== "success") {
                throw new Error(result.message || "PDF 진단에 실패했습니다.");
            }

            const diag = result.data;
            state.diagnosticData = diag;
            console.log(`[INSPECT] 진단 완료: 전체 ${diag.total_pages}페이지 (텍스트: ${diag.text_pages_count}, 스캔본: ${diag.scanned_pages_count})`);

            // UI 업데이트
            elements.diagFilename.textContent = diag.filename;
            elements.diagMeta.textContent = `크기: ${diag.file_size_mb}MB | 총 ${diag.total_pages}페이지`;
            elements.diagTotalPages.textContent = diag.total_pages;
            elements.diagTextPages.textContent = diag.text_pages_count;
            elements.diagScannedPages.textContent = diag.scanned_pages_count;
            elements.pageRangeInput.value = "";
            elements.pageRangeInput.placeholder = `예: ${diag.recommended_range} (비워두면 전체 적용)`;
            elements.recommendedRangeHint.textContent = `권장 범위: ${diag.recommended_range}`;

            // 경고 메시지 표시
            if (diag.warnings && diag.warnings.length > 0) {
                elements.diagWarningsList.innerHTML = diag.warnings.map(w => `<li>${escapeHtml(w)}</li>`).join("");
                elements.diagWarningsBox.classList.remove("hidden");
            } else {
                elements.diagWarningsBox.classList.add("hidden");
            }

            // 스캔본이 많을 경우 필기 모드 자동 활성화 추천
            if (diag.scanned_pages_count > 0) {
                elements.handwritingModeToggle.checked = true;
            }

            elements.diagnosticCard.classList.remove("hidden");
            showToast("PDF 분석이 완료되었습니다. 요약 범위를 확인해 주세요.", "success");

        } catch (err) {
            console.error("[ERROR] PDF 진단 오류:", err);
            showToast(err.message || "PDF 분석 중 오류가 발생했습니다.", "error");
        } finally {
            hideLoading();
        }
    }

    // 4. 개념 요약 생성
    elements.btnSummarize.addEventListener("click", async () => {
        if (!state.selectedFile) {
            showToast("먼저 PDF 파일을 선택해 주세요.", "error");
            return;
        }

        const pages = elements.pageRangeInput.value.trim() || "all";
        const handwritingMode = elements.handwritingModeToggle.checked;

        console.log(`[SUMMARIZE] 개념 요약 요청 시작 (페이지: ${pages}, 필기 모드: ${handwritingMode})`);
        showLoading("AI가 교재 핵심 개념을 요약하고 있습니다...\n(시험 직전 단권화 노트 생성 중)");

        const formData = new FormData();
        formData.append("file", state.selectedFile);
        formData.append("pages", pages);
        formData.append("handwriting_mode", handwritingMode);

        try {
            const res = await fetch("/summarize", {
                method: "POST",
                body: formData
            });

            const result = await parseResponseJson(res);
            if (!res.ok || result.status !== "success") {
                throw new Error(result.message || "요약 생성에 실패했습니다.");
            }

            state.summaryText = result.summary;
            console.log(`[SUMMARIZE] 개념 요약 수신 완료 (길이: ${result.summary.length}자)`);

            // UI 렌더링
            elements.summaryContent.innerHTML = renderMarkdown(result.summary);
            elements.summaryModelBadge.textContent = result.model || "Gemini";
            elements.printDocMeta.textContent = `문서: ${state.selectedFile.name} (선택 페이지: ${result.pages.join(", ")}) | 요약 일자: ${new Date().toLocaleDateString("ko-KR")}`;

            // 요약 섹션 및 Q&A 섹션 노출
            elements.summarySection.classList.remove("hidden");
            elements.qaSection.classList.remove("hidden");

            // 요약 영역으로 부드럽게 스크롤 이동
            elements.summarySection.scrollIntoView({ behavior: "smooth" });
            showToast("핵심 개념 요약이 완성되었습니다!", "success");

        } catch (err) {
            console.error("[ERROR] 개념 요약 실패:", err);
            showToast(err.message || "요약 생성 중 문제가 발생했습니다.", "error");
        } finally {
            hideLoading();
        }
    });

    // 5. PDF 인쇄/저장 (window.print)
    elements.btnPrintPdf.addEventListener("click", () => {
        console.log("[PRINT] PDF 인쇄/저장 창 호출");
        window.print();
    });

    // 6. 실전 퀴즈 생성
    elements.btnGenerateQuiz.addEventListener("click", async () => {
        if (!state.summaryText) {
            showToast("먼저 개념 요약을 진행해 주세요.", "error");
            return;
        }

        console.log("[QUIZ] 퀴즈 생성 요청 시작");
        showLoading("요약된 핵심 개념을 바탕으로 실전 퀴즈 5문항을 생성 중입니다...");

        try {
            const res = await fetch("/quiz", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ summary: state.summaryText })
            });

            const result = await parseResponseJson(res);
            if (!res.ok || result.status !== "success") {
                throw new Error(result.message || "퀴즈 생성에 실패했습니다.");
            }

            state.quizzes = result.quizzes || [];
            console.log(`[QUIZ] 퀴즈 데이터 수신 완료 (${state.quizzes.length}문항)`);

            renderQuizList(state.quizzes);
            elements.quizSection.classList.remove("hidden");
            elements.quizScoreBadge.classList.add("hidden");
            elements.quizSection.scrollIntoView({ behavior: "smooth" });

            showToast("실전 대비 퀴즈가 생성되었습니다!", "success");

        } catch (err) {
            console.error("[ERROR] 퀴즈 생성 실패:", err);
            showToast(err.message || "퀴즈 생성 중 오류가 발생했습니다.", "error");
        } finally {
            hideLoading();
        }
    });

    // 퀴즈 렌더링 헬퍼
    function renderQuizList(quizzes) {
        if (!quizzes || quizzes.length === 0) {
            elements.quizList.innerHTML = "<p>생성된 퀴즈가 없습니다.</p>";
            return;
        }

        elements.quizList.innerHTML = quizzes.map((q, idx) => {
            const qNum = idx + 1;
            const isMultiple = q.type === "multiple_choice";
            const typeLabel = isMultiple ? "객관식" : "단답형";

            let inputHtml = "";
            if (isMultiple && q.options) {
                inputHtml = `
                    <div class="quiz-options">
                        ${q.options.map((opt, optIdx) => `
                            <label class="quiz-option-label">
                                <input type="radio" name="quiz_${q.id}" value="${escapeHtml(opt)}">
                                <span>${escapeHtml(opt)}</span>
                            </label>
                        `).join("")}
                    </div>
                `;
            } else {
                inputHtml = `
                    <div class="form-group">
                        <input type="text" name="quiz_${q.id}" class="form-control" placeholder="정답 키워드를 입력하세요" autocomplete="off">
                    </div>
                `;
            }

            return `
                <div class="quiz-item" id="quizItem_${q.id}">
                    <div class="quiz-header">
                        <span class="quiz-type-badge">${typeLabel}</span>
                        <span class="quiz-source-tag">[출처: p.${q.source_page || "요약본"}]</span>
                    </div>
                    <div class="quiz-question">${qNum}. ${escapeHtml(q.question)}</div>
                    ${inputHtml}
                    <div class="quiz-feedback hidden" id="feedback_${q.id}"></div>
                </div>
            `;
        }).join("");
    }

    // 퀴즈 채점 제출
    elements.quizForm.addEventListener("submit", (e) => {
        e.preventDefault();
        if (!state.quizzes || state.quizzes.length === 0) return;

        let correctCount = 0;
        const totalCount = state.quizzes.length;

        state.quizzes.forEach(q => {
            const feedbackEl = document.getElementById(`feedback_${q.id}`);
            let userAnswer = "";

            if (q.type === "multiple_choice") {
                const selectedRadio = elements.quizForm.querySelector(`input[name="quiz_${q.id}"]:checked`);
                userAnswer = selectedRadio ? selectedRadio.value.trim() : "";
            } else {
                const textInput = elements.quizForm.querySelector(`input[name="quiz_${q.id}"]`);
                userAnswer = textInput ? textInput.value.trim() : "";
            }

            const isCorrect = normalizeAnswer(userAnswer) === normalizeAnswer(q.answer);

            if (isCorrect) {
                correctCount++;
                feedbackEl.className = "quiz-feedback correct";
                feedbackEl.innerHTML = `
                    <strong>정답입니다! 👏</strong><br>
                    <span><strong>근거:</strong> ${escapeHtml(q.explanation || "")} (출처: p.${q.source_page || "요약본"})</span>
                `;
            } else {
                feedbackEl.className = "quiz-feedback incorrect";
                feedbackEl.innerHTML = `
                    <strong>오답입니다. ❌</strong> (제출: ${userAnswer ? escapeHtml(userAnswer) : "미입력"})<br>
                    <span><strong>정답:</strong> ${escapeHtml(q.answer)}</span><br>
                    <span><strong>해설:</strong> ${escapeHtml(q.explanation || "")} (출처: p.${q.source_page || "요약본"})</span>
                `;
            }
            feedbackEl.classList.remove("hidden");
        });

        elements.quizScoreText.textContent = `${correctCount} / ${totalCount} (${Math.round((correctCount / totalCount) * 100)}점)`;
        elements.quizScoreBadge.classList.remove("hidden");
        showToast(`채점 완료! 결과: ${correctCount}/${totalCount}`, "info");
    });

    function normalizeAnswer(str) {
        return (str || "").replace(/\s+/g, "").toLowerCase();
    }

    // 7. 밀착 Q&A 챗
    elements.chatForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const question = elements.chatInput.value.trim();
        if (!question) return;

        if (!state.summaryText) {
            showToast("참조할 교재 요약본이 없습니다. 먼저 요약을 진행해 주세요.", "error");
            return;
        }

        console.log(`[ASK] 질문 전송: "${question}"`);
        appendChatMessage("user", question);
        elements.chatInput.value = "";

        // 로딩 버블 생성
        const loadingBubble = appendChatMessage("bot", "교재 내용을 팩트체크하며 답변을 정리하고 있습니다...");

        try {
            const res = await fetch("/ask", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    question: question,
                    context: state.summaryText
                })
            });

            const result = await parseResponseJson(res);
            if (!res.ok || result.status !== "success") {
                throw new Error(result.message || "답변을 가져오지 못했습니다.");
            }

            console.log(`[ASK] 답변 수신 완료 (길이: ${result.answer.length}자)`);
            loadingBubble.innerHTML = renderMarkdown(result.answer);

        } catch (err) {
            console.error("[ERROR] 질의응답 실패:", err);
            loadingBubble.innerHTML = `<span style="color: #ef4444;">오류가 발생했습니다: ${escapeHtml(err.message)}</span>`;
        }
    });

    function appendChatMessage(sender, text) {
        const msgDiv = document.createElement("div");
        msgDiv.className = `chat-message ${sender}-message`;

        const bubbleDiv = document.createElement("div");
        bubbleDiv.className = "chat-bubble";
        bubbleDiv.textContent = text;

        msgDiv.appendChild(bubbleDiv);
        elements.chatBox.appendChild(msgDiv);
        elements.chatBox.scrollTop = elements.chatBox.scrollHeight;
        return bubbleDiv;
    }

    // 8. 미니 마크다운 파서 (외부 라이브러리 없이 안전한 HTML, 표 및 컬러 하이라이트 렌더링)
    function renderMarkdown(md) {
        if (!md) return "";
        let html = escapeHtml(md);

        // 1. 마크다운 표(Table) 파싱
        html = parseMarkdownTables(html);

        // 2. 시각적 컬러 강조 태그 변환 (&lt;red&gt;, &lt;blue&gt;, &lt;mark&gt;)
        html = html.replace(/&lt;red&gt;(.*?)&lt;\/red&gt;/gim, '<span class="text-red"><strong>$1</strong></span>');
        html = html.replace(/&lt;blue&gt;(.*?)&lt;\/blue&gt;/gim, '<span class="text-blue"><strong>$1</strong></span>');
        html = html.replace(/&lt;mark&gt;(.*?)&lt;\/mark&gt;/gim, '<mark class="highlight-yellow">$1</mark>');
        html = html.replace(/&lt;yellow&gt;(.*?)&lt;\/yellow&gt;/gim, '<mark class="highlight-yellow">$1</mark>');
        html = html.replace(/==(.*?)==/gim, '<mark class="highlight-yellow">$1</mark>');

        // 3. 헤딩 변환
        html = html.replace(/^### (.*$)/gim, "<h3>$1</h3>");
        html = html.replace(/^## (.*$)/gim, "<h2>$1</h2>");
        html = html.replace(/^# (.*$)/gim, "<h1>$1</h1>");

        // 4. 볼드 및 인라인 코드
        html = html.replace(/\*\*(.*?)\*\*/gim, "<strong>$1</strong>");
        html = html.replace(/`([^`]+)`/gim, "<code>$1</code>");

        // 5. 특별 콜아웃 박스 (교수님 필기 박스 🚨 및 상단 안내 박스 📌)
        html = html.replace(/^&gt; 🚨 (.*$)/gim, '<div class="teacher-note-box"><span class="note-icon">🚨</span> $1</div>');
        html = html.replace(/^&gt; 📌 (.*$)/gim, '<div class="intro-guide-box"><span class="guide-icon">📌</span> $1</div>');
        html = html.replace(/^&gt; (.*$)/gim, "<blockquote>$1</blockquote>");

        // 6. 불릿 목록
        html = html.replace(/^\- (.*$)/gim, "<li>$1</li>");
        html = html.replace(/(<li>.*<\/li>)/gim, "<ul>$1</ul>");

        // 7. 줄바꿈 보존 (단, 박스 및 테이블 태그 영역은 불필요한 <br> 추가 방지)
        html = html.replace(/\n\n/gim, "<br><br>");
        html = html.replace(/\n/gim, "<br>");
        html = html.replace(/<\/table><br>/gi, "</table>");
        html = html.replace(/<\/div><br>/gi, "</div>");
        html = html.replace(/<\/div><br><br>/gi, "</div><br>");

        return html;
    }

    function parseMarkdownTables(text) {
        const lines = text.split("\n");
        const result = [];
        let tableLines = [];
        let inTable = false;

        function buildTable(rows) {
            if (rows.length === 0) return "";
            let headerCells = null;
            const bodyRows = [];

            for (const r of rows) {
                const trimmed = r.trim();
                const cells = trimmed.split("|").slice(1, -1).map(c => c.trim());
                if (cells.length === 0) continue;
                // 구분 행 (---) 검사
                const isDivider = cells.every(c => /^[-:\s]+$/.test(c));
                if (isDivider) continue;

                if (!headerCells) {
                    headerCells = cells;
                } else {
                    bodyRows.push(cells);
                }
            }

            if (!headerCells) return rows.join("\n");

            let tableHtml = '<div class="table-responsive"><table class="concept-table"><thead><tr>';
            headerCells.forEach(h => {
                tableHtml += `<th>${h}</th>`;
            });
            tableHtml += '</tr></thead><tbody>';
            bodyRows.forEach(row => {
                tableHtml += '<tr>';
                row.forEach(c => {
                    tableHtml += `<td>${c}</td>`;
                });
                tableHtml += '</tr>';
            });
            tableHtml += '</tbody></table></div>';
            return tableHtml;
        }

        for (let i = 0; i < lines.length; i++) {
            const line = lines[i].trim();
            if (line.startsWith("|") && line.endsWith("|")) {
                inTable = true;
                tableLines.push(lines[i]);
            } else {
                if (inTable) {
                    result.push(buildTable(tableLines));
                    tableLines = [];
                    inTable = false;
                }
                result.push(lines[i]);
            }
        }

        if (inTable && tableLines.length > 0) {
            result.push(buildTable(tableLines));
        }

        return result.join("\n");
    }

    function escapeHtml(text) {
        const div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }

    // 9. UI 헬퍼: 로딩 및 토스트
    function showLoading(msg) {
        elements.loadingMessage.textContent = msg || "처리 중입니다...";
        elements.loadingOverlay.classList.remove("hidden");
    }

    function hideLoading() {
        elements.loadingOverlay.classList.add("hidden");
    }

    function showToast(message, type = "info") {
        const toast = document.createElement("div");
        toast.className = `toast toast-${type}`;
        toast.textContent = message;

        elements.toastContainer.appendChild(toast);
        setTimeout(() => {
            toast.remove();
        }, 4000);
    }

    // 시작 시 서버 상태 확인
    checkServerHealth();
});
