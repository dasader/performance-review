import { useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { ApiError, del, get, post, put, type AdminSubfield, type Field } from "../api";
import { lintQuery, type LintResult } from "../lib/queryLint";

interface SubfieldBody {
  field_id: number;
  name: string;
  query: string;
  query_kci: string | null;
  active: boolean;
}

// 추가·편집을 하나의 모달 폼으로 합친다. mode로 제출 방식(POST/PUT)만 갈린다.
interface ModalDraft {
  mode: "add" | "edit";
  id?: number;
  fieldId: number | "";
  name: string;
  query: string;
  queryKci: string;
  active: boolean;
}

const emptyModalDraft: ModalDraft = {
  mode: "add",
  fieldId: "",
  name: "",
  query: "",
  queryKci: "",
  active: true,
};

// 검색식에 개행은 의미가 없다(줄바꿈은 textarea에서 보기 편하라고 있는 것) — 저장 전 공백 하나로 접는다.
function normalizeQuery(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

// 내용에 맞춰 textarea 높이를 늘린다. ref 콜백이라 마운트 시(편집 진입 등)에도 바로 적용된다.
function autoGrow(el: HTMLTextAreaElement | null) {
  if (!el) return;
  el.style.height = "auto";
  el.style.height = `${el.scrollHeight}px`;
}

export default function SubfieldEditor({
  adminKey,
  fields,
  onChanged,
  onUnauthorized,
  onItemsLoaded,
}: {
  adminKey: string;
  fields: Field[];
  onChanged: () => void;
  onUnauthorized: () => void;
  // 대시보드(/admin/dashboard)는 active 여부를 안 주므로, 이미 이 컴포넌트가 받아오는
  // /admin/subfields 결과(active 포함)를 부모로 끌어올려 재사용한다 — 중복 호출 방지.
  onItemsLoaded?: (items: AdminSubfield[]) => void;
}) {
  const [items, setItems] = useState<AdminSubfield[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [deleteConflict, setDeleteConflict] = useState<{ id: number; message: string } | null>(null);

  const [modal, setModal] = useState<ModalDraft | null>(null);
  const [modalError, setModalError] = useState<string | null>(null);
  const [modalSaving, setModalSaving] = useState(false);
  // 검색식 규칙 도움말(i 버튼) 펼침 상태. 모달은 한 번에 하나만 열리므로 상태 하나로 충분하다.
  const [modalHelp, setModalHelp] = useState({ openalex: false, kci: false });
  const fieldSelectRef = useRef<HTMLSelectElement>(null);

  // 표의 행마다 부르므로 선형 탐색을 두면 55행 × 렌더마다 훑는다.
  const fieldNames = useMemo(() => new Map(fields.map((f) => [f.id, f.name])), [fields]);
  const fieldName = (id: number) => fieldNames.get(id) ?? `분야 #${id}`;

  const load = async () => {
    try {
      const list = await get<AdminSubfield[]>("/admin/subfields", adminKey);
      setItems(list);
      setListError(null);
      onItemsLoaded?.(list);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onUnauthorized();
      setListError(e instanceof Error ? e.message : "목록을 불러오지 못했습니다.");
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 토글·"대신 비활성화"가 모두 "전체 필드를 다시 PUT" 한 형태라 저장 로직을 하나로 묶는다.
  const saveSubfield = async (id: number, body: SubfieldBody): Promise<boolean> => {
    setBusyId(id);
    try {
      await put(`/admin/subfields/${id}`, body, adminKey);
      await load();
      onChanged();
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        onUnauthorized();
        return false;
      }
      setActionError(e instanceof Error ? e.message : "저장에 실패했습니다.");
      return false;
    } finally {
      setBusyId(null);
    }
  };

  const openAdd = () => {
    setModal({ ...emptyModalDraft });
    setModalError(null);
    setModalHelp({ openalex: false, kci: false });
  };

  const openEdit = (item: AdminSubfield) => {
    setModal({
      mode: "edit",
      id: item.id,
      fieldId: item.field_id,
      name: item.name,
      query: item.query,
      queryKci: item.query_kci ?? "",
      active: item.active,
    });
    setModalError(null);
    setModalHelp({ openalex: false, kci: false });
  };

  const closeModal = () => {
    if (modalSaving) return; // 저장 중에는 닫히지 않는다.
    setModal(null);
  };

  const handleModalSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!modal) return;
    setModalError(null);
    if (modal.fieldId === "") {
      setModalError("분야를 선택하세요.");
      return;
    }
    const query = normalizeQuery(modal.query);
    const queryKci = normalizeQuery(modal.queryKci);
    if (!modal.name.trim() || !query) {
      setModalError("세부기술명과 검색식은 비워둘 수 없습니다.");
      return;
    }
    // 저장 버튼이 이미 오류 시 비활성화되지만, 방어적으로 한 번 더 막는다.
    if (lintQuery(query, "openalex").errors.length > 0 || lintQuery(queryKci, "kci").errors.length > 0) {
      setModalError("검색식 문법 오류를 먼저 수정하세요.");
      return;
    }

    setModalSaving(true);
    try {
      // active를 add에서 빠뜨리면 "활성 여부" 토글을 꺼도 백엔드 기본값(True)으로
      // 저장된다 — 그 토글은 add 모드에서도 렌더되므로 조용히 무시되는 셈이었다.
      const payload: SubfieldBody = {
        field_id: modal.fieldId,
        name: modal.name.trim(),
        query,
        query_kci: queryKci || null,
        active: modal.active,
      };
      if (modal.mode === "add") {
        await post("/admin/subfields", payload, adminKey);
      } else {
        await put(`/admin/subfields/${modal.id}`, payload, adminKey);
      }
      await load();
      onChanged();
      setModal(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onUnauthorized();
      setModalError(e instanceof Error ? e.message : "저장에 실패했습니다.");
    } finally {
      setModalSaving(false);
    }
  };

  const toggleActive = (item: AdminSubfield) =>
    saveSubfield(item.id, {
      field_id: item.field_id,
      name: item.name,
      query: item.query,
      query_kci: item.query_kci,
      active: !item.active,
    });

  const handleDelete = async (item: AdminSubfield) => {
    if (!confirm(`'${item.name}'을(를) 삭제할까요? 이 작업은 되돌릴 수 없습니다.`)) return;
    setDeleteConflict(null);
    setBusyId(item.id);
    try {
      await del(`/admin/subfields/${item.id}`, adminKey);
      await load();
      onChanged();
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 401) return onUnauthorized();
        if (e.status === 409) {
          setDeleteConflict({ id: item.id, message: e.message });
          return;
        }
      }
      setActionError(e instanceof Error ? e.message : "삭제에 실패했습니다.");
    } finally {
      setBusyId(null);
    }
  };

  // 린트 계산을 값이 바뀔 때로 한정한다. 세부기술명 입력·활성 토글처럼 검색식과
  // 무관한 상태 변경에서는 다시 돌지 않는다(표 재렌더 자체는 여전히 일어난다 —
  // 모달과 표가 한 컴포넌트라 setModal이 둘 다 다시 그린다).
  const modalQuery = modal?.query;
  const modalQueryKci = modal?.queryKci;
  const openalexLint = useMemo(
    () => (modalQuery === undefined ? null : lintQuery(modalQuery, "openalex")),
    [modalQuery],
  );
  const kciLint = useMemo(
    () => (modalQueryKci === undefined ? null : lintQuery(modalQueryKci, "kci")),
    [modalQueryKci],
  );

  return (
    <section className="border border-border bg-surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="font-display text-lg font-semibold text-accent">세부기술 · 검색식</h2>
          <p className="mt-1 text-xs text-muted">
            검색식을 바꾸면 이미 수집된 연도는 다음 실행 상태 표에서 "갱신 필요"로 표시됩니다.
          </p>
        </div>
        <button
          type="button"
          onClick={openAdd}
          className="shrink-0 border border-ink bg-ink px-4 py-2 text-sm font-medium text-paper transition-colors hover:bg-ink/90"
        >
          세부기술 추가
        </button>
      </div>

      {deleteConflict && (
        <div className="mt-4 border border-warning/40 bg-warning/5 p-4 text-sm">
          <p className="text-warning">{deleteConflict.message}</p>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              onClick={() => {
                const item = items?.find((i) => i.id === deleteConflict.id);
                if (item) toggleActive(item).then((ok) => ok && setDeleteConflict(null));
              }}
              className="border border-border px-3 py-1.5 text-xs text-ink-light hover:border-accent hover:text-accent"
            >
              대신 비활성화
            </button>
            <button
              type="button"
              onClick={() => setDeleteConflict(null)}
              className="px-3 py-1.5 text-xs text-muted hover:text-ink"
            >
              닫기
            </button>
          </div>
        </div>
      )}

      {listError && <p className="mt-4 text-sm text-danger">{listError}</p>}
      {!items && !listError && <p className="mt-4 text-sm text-muted">불러오는 중…</p>}

      {items && items.length > 0 && (
        <div className="mt-4 overflow-x-auto border-t border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted">
                <th className="py-2 pr-3 font-medium">분야</th>
                <th className="py-2 pr-3 font-medium">세부기술</th>
                <th className="min-w-[14rem] py-2 pr-3 font-medium">검색식</th>
                <th className="min-w-[12rem] py-2 pr-3 font-medium">KCI 검색식</th>
                <th className="py-2 pr-3 font-medium">활성</th>
                <th className="py-2 font-medium">
                  <span className="sr-only">동작</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const isBusy = busyId === item.id;
                return (
                  <tr key={item.id} className="border-b border-border-light align-top">
                    <td className="py-3 pr-3 text-xs text-muted">{fieldName(item.field_id)}</td>
                    <td className="py-3 pr-3 font-medium text-ink">{item.name}</td>
                    <td className="py-3 pr-3">
                      <p className="whitespace-pre-wrap break-words font-mono text-xs text-ink-light">
                        {item.query}
                      </p>
                    </td>
                    <td className="py-3 pr-3">
                      {item.query_kci ? (
                        <p className="whitespace-pre-wrap break-words font-mono text-xs text-faint">
                          {item.query_kci}
                        </p>
                      ) : (
                        <p className="text-xs text-faint">(공통 사용)</p>
                      )}
                    </td>
                    <td className="py-3 pr-3">
                      {/* min-w — 활성/비활성 라벨 글자 수가 달라도(2자/3자) 버튼 폭이 고정되어
                          토글할 때마다 오른쪽 열(동작 버튼들)이 좌우로 밀리지 않는다. */}
                      <button
                        type="button"
                        role="switch"
                        aria-checked={item.active}
                        disabled={isBusy}
                        onClick={() => toggleActive(item)}
                        className={`min-w-[4.5rem] border px-2 py-1 text-center text-xs disabled:opacity-40 ${
                          item.active
                            ? "border-positive/40 text-positive"
                            : "border-border text-faint"
                        }`}
                      >
                        {item.active ? "활성" : "비활성"}
                      </button>
                    </td>
                    <td className="py-3 text-right whitespace-nowrap">
                      <button
                        type="button"
                        onClick={() => openEdit(item)}
                        className="mr-2 border border-border px-2 py-1 text-xs text-ink-light hover:border-accent hover:text-accent"
                      >
                        편집
                      </button>
                      <button
                        type="button"
                        disabled={isBusy}
                        onClick={() => handleDelete(item)}
                        className="border border-border px-2 py-1 text-xs text-danger hover:border-danger disabled:opacity-40"
                      >
                        삭제
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {actionError && <p className="mt-2 text-sm text-danger">{actionError}</p>}
        </div>
      )}
      {items && items.length === 0 && (
        <p className="mt-4 text-sm text-muted">등록된 세부기술이 없습니다.</p>
      )}

      {modal && openalexLint && kciLint && (
        <Modal
          titleId="subfield-modal-title"
          title={modal.mode === "add" ? "세부기술 추가" : "세부기술 편집"}
          onClose={closeModal}
          closeDisabled={modalSaving}
          initialFocusRef={fieldSelectRef}
        >
          <form onSubmit={handleModalSubmit} className="flex flex-col gap-4">
            <div>
              <label htmlFor="modal-field-id" className="mb-1 block text-xs font-medium text-ink-light">
                분야
              </label>
              <select
                id="modal-field-id"
                ref={fieldSelectRef}
                value={modal.fieldId}
                onChange={(e) =>
                  setModal((m) => m && { ...m, fieldId: e.target.value ? Number(e.target.value) : "" })
                }
                className="w-full border border-border bg-surface px-3 py-2 text-sm text-ink focus:border-accent"
              >
                <option value="">분야 선택</option>
                {fields.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.name}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label htmlFor="modal-name" className="mb-1 block text-xs font-medium text-ink-light">
                세부기술명
              </label>
              <input
                id="modal-name"
                value={modal.name}
                onChange={(e) => setModal((m) => m && { ...m, name: e.target.value })}
                className="w-full border border-border bg-surface px-3 py-2 text-sm text-ink focus:border-accent"
              />
            </div>

            <div>
              <div className="mb-1 flex items-center gap-1.5">
                <label htmlFor="modal-query" className="block text-xs font-medium text-ink-light">
                  검색식 (OpenAlex)
                </label>
                <QueryHelpToggle
                  source="openalex"
                  open={modalHelp.openalex}
                  onToggle={() => setModalHelp((h) => ({ ...h, openalex: !h.openalex }))}
                  panelId="query-help-openalex-modal"
                />
              </div>
              {modalHelp.openalex && <QueryHelpPanel source="openalex" panelId="query-help-openalex-modal" />}
              <textarea
                id="modal-query"
                ref={autoGrow}
                onInput={(e) => autoGrow(e.currentTarget)}
                rows={3}
                value={modal.query}
                onChange={(e) => setModal((m) => m && { ...m, query: e.target.value })}
                className="mt-1 w-full resize-y border border-border bg-surface px-3 py-2 font-mono text-sm text-ink focus:border-accent"
              />
              <QueryLintFeedback result={openalexLint} valueTrimmed={modal.query.trim()} />
            </div>

            <div>
              <div className="mb-1 flex items-center gap-1.5">
                <label htmlFor="modal-query-kci" className="block text-xs font-medium text-ink-light">
                  KCI 검색식 (비우면 공통값 사용)
                </label>
                <QueryHelpToggle
                  source="kci"
                  open={modalHelp.kci}
                  onToggle={() => setModalHelp((h) => ({ ...h, kci: !h.kci }))}
                  panelId="query-help-kci-modal"
                />
              </div>
              {modalHelp.kci && <QueryHelpPanel source="kci" panelId="query-help-kci-modal" />}
              <textarea
                id="modal-query-kci"
                ref={autoGrow}
                onInput={(e) => autoGrow(e.currentTarget)}
                rows={3}
                value={modal.queryKci}
                onChange={(e) => setModal((m) => m && { ...m, queryKci: e.target.value })}
                className="mt-1 w-full resize-y border border-border bg-surface px-3 py-2 font-mono text-sm text-ink focus:border-accent"
              />
              <QueryLintFeedback result={kciLint} valueTrimmed={modal.queryKci.trim()} />
            </div>

            <div>
              <span id="modal-active-label" className="mb-1 block text-xs font-medium text-ink-light">
                활성 여부
              </span>
              <button
                type="button"
                role="switch"
                aria-checked={modal.active}
                aria-labelledby="modal-active-label"
                onClick={() => setModal((m) => m && { ...m, active: !m.active })}
                className={`min-w-[4.5rem] border px-2 py-1 text-center text-xs ${
                  modal.active ? "border-positive/40 text-positive" : "border-border text-faint"
                }`}
              >
                {modal.active ? "활성" : "비활성"}
              </button>
            </div>

            {modalError && <p className="text-sm text-danger">{modalError}</p>}

            <div className="mt-1 flex justify-end gap-2">
              <button
                type="button"
                onClick={closeModal}
                disabled={modalSaving}
                className="border border-border px-4 py-2 text-sm text-ink-light hover:border-accent hover:text-accent disabled:opacity-40"
              >
                취소
              </button>
              <button
                type="submit"
                disabled={modalSaving || openalexLint.errors.length > 0 || kciLint.errors.length > 0}
                className="border border-ink bg-ink px-4 py-2 text-sm font-medium text-paper transition-colors hover:bg-ink/90 disabled:opacity-40"
              >
                {modalSaving ? "저장 중…" : "저장"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </section>
  );
}

// 검색식 문법 검사 결과를 보여준다. 오류·경고는 색뿐 아니라 "[오류]"/"[경고]" 텍스트 라벨을
// 함께 붙인다(색만으로 구분하지 않음). 입력이 비어 있으면 아무것도 표시하지 않는다 — 문법
// 검사가 아니라 "실제로 몇 건이 걸리는지"는 여전히 미리보기로만 확인 가능함을 분명히 한다.
function QueryLintFeedback({ result, valueTrimmed }: { result: LintResult; valueTrimmed: string }) {
  if (!valueTrimmed) return null;
  if (result.errors.length === 0 && result.warnings.length === 0) {
    return (
      <p className="mt-1 text-xs text-positive">
        문법 오류 없음 <span className="text-muted">— 실제로 몇 건이 걸리는지는 미리보기로 확인하세요.</span>
      </p>
    );
  }
  return (
    <ul className="mt-1 space-y-0.5 text-xs">
      {result.errors.map((issue) => (
        <li key={issue.code} className="text-danger">
          <span className="font-medium">[오류]</span> {issue.message}
        </li>
      ))}
      {result.warnings.map((issue) => (
        <li key={issue.code} className="text-warning">
          <span className="font-medium">[경고]</span> {issue.message}
        </li>
      ))}
    </ul>
  );
}

// 세부기술 추가/편집 공용 모달. 라이브러리 없이 직접 구현:
// - Esc·명시적 취소/닫기 버튼으로만 닫힘(저장 중에는 무시). 배경(오버레이) 클릭으로는
//   닫히지 않는다 — 검색식을 길게 입력하다 실수로 바깥을 클릭하면 입력이 통째로 날아가는
//   사고를 막기 위함이다.
// - 열릴 때 initialFocusRef로 포커스, 닫힐 때(언마운트) 트리거였던 요소로 포커스 복귀
//   (모달을 연 순간의 document.activeElement가 곧 그 트리거 버튼이므로 별도로 전달받지 않는다)
// - role=dialog + aria-modal + aria-labelledby, 열려 있는 동안 배경 스크롤 잠금
// - 내용이 길면 모달 내부만 스크롤(max-h)
// ponytail: 포커스 트랩은 없다 — aria-modal="true"는 어떤 브라우저에서도 포커스를
// 가두지 않으므로, Tab을 계속 누르면 오버레이 뒤의 편집/삭제 버튼으로 빠져나간다.
// 고치려면 <dialog>+showModal()로 바꾸는 쪽이 맞다(브라우저가 트랩·Esc·스크롤 잠금을
// 다 해주므로 위 수동 구현 대부분이 삭제된다). 실제 브라우저에서 확인할 수 있을 때 하자.
function Modal({
  titleId,
  title,
  onClose,
  closeDisabled,
  initialFocusRef,
  children,
}: {
  titleId: string;
  title: string;
  onClose: () => void;
  closeDisabled: boolean;
  initialFocusRef: RefObject<HTMLElement | null>;
  children: ReactNode;
}) {
  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    // rAF로 한 프레임 미뤄야 방금 마운트된 select에 포커스가 안정적으로 잡힌다.
    const frame = requestAnimationFrame(() => initialFocusRef.current?.focus());
    return () => {
      cancelAnimationFrame(frame);
      document.body.style.overflow = prevOverflow;
      previouslyFocused?.focus();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape" && !closeDisabled) onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [closeDisabled, onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/50 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="flex max-h-[85vh] w-full max-w-lg flex-col border border-border bg-surface shadow-xl"
      >
        <div className="flex shrink-0 items-center justify-between border-b border-border px-5 py-3">
          <h2 id={titleId} className="font-display text-base font-semibold text-ink">
            {title}
          </h2>
          <button
            type="button"
            onClick={onClose}
            disabled={closeDisabled}
            aria-label="닫기"
            className="text-lg leading-none text-muted hover:text-ink disabled:opacity-40"
          >
            ×
          </button>
        </div>
        <div className="overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </div>
  );
}

function QueryHelpToggle({
  source,
  open,
  onToggle,
  panelId,
}: {
  source: "openalex" | "kci";
  open: boolean;
  onToggle: () => void;
  panelId: string;
}) {
  const label = source === "openalex" ? "OpenAlex 검색식 규칙 보기" : "KCI 검색식 규칙 보기";
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      aria-controls={panelId}
      aria-label={label}
      className="inline-flex h-4 w-4 shrink-0 items-center justify-center border border-border font-mono text-[10px] leading-none text-muted hover:border-accent hover:text-accent"
    >
      i
    </button>
  );
}

function QueryHelpPanel({ source, panelId }: { source: "openalex" | "kci"; panelId: string }) {
  if (source === "openalex") {
    return (
      <div id={panelId} className="mt-2 border border-accent-border bg-accent-light p-3 text-xs text-ink-light">
        <p className="font-medium text-ink">OpenAlex 검색식 규칙</p>
        <ul className="mt-2 list-disc space-y-1 pl-4">
          <li>
            <code className="font-mono text-ink">AND</code> · <code className="font-mono text-ink">OR</code> ·{" "}
            <code className="font-mono text-ink">NOT</code> · 괄호 ·{" "}
            <code className="font-mono text-ink">"구문 검색"</code>을 지원합니다.
          </li>
          <li>
            공백은 암묵적 <code className="font-mono text-ink">AND</code>로 처리됩니다.
          </li>
          <li>
            콤마(<code className="font-mono text-ink">,</code>)와 파이프(<code className="font-mono text-ink">|</code>)는{" "}
            <span className="font-medium text-warning">자동으로 공백으로 치환됩니다</span> — 대신{" "}
            <code className="font-mono text-ink">AND</code>/<code className="font-mono text-ink">OR</code>를 쓰세요.
          </li>
          <li>
            검색 대상은 제목과 초록입니다(<code className="font-mono text-ink">title_and_abstract</code>).
          </li>
          <li>한국 소속 저자 필터와 연도 필터는 자동으로 적용되므로 검색식에 넣지 마세요.</li>
        </ul>
        <p className="mt-3 font-medium text-ink">예시 (2025년 한국 논문 기준 실측)</p>
        <ul className="mt-1 space-y-0.5">
          <li>
            <code className="font-mono text-ink">semiconductor AND memory</code>{" "}
            <span className="text-muted">→ 206건</span>
          </li>
          <li>
            <code className="font-mono text-ink">semiconductor NOT memory</code>{" "}
            <span className="text-muted">→ 1,736건</span>
          </li>
          <li>
            <code className="font-mono text-ink">"memory semiconductor"</code>{" "}
            <span className="text-muted">→ 5건 (구문 검색)</span>
          </li>
          <li>
            <code className="font-mono text-ink">(memory OR flash) AND semiconductor</code>{" "}
            <span className="text-muted">→ 208건</span>
          </li>
        </ul>

        <p className="mt-3 font-medium text-ink">괄호 중첩</p>
        <p className="mt-1">
          괄호 안에 <code className="font-mono text-ink">AND</code>/<code className="font-mono text-ink">OR</code>를
          중첩할 수 있고, 그룹끼리 조합하거나 <code className="font-mono text-ink">NOT</code>과 섞어도 됩니다.
        </p>
        <ul className="mt-1 space-y-0.5">
          <li>
            <code className="font-mono text-ink">(memory OR flash) AND (semiconductor OR device)</code>{" "}
            <span className="text-muted">→ 1,055건 (그룹 2개)</span>
          </li>
          <li>
            <code className="font-mono text-ink">((memory OR flash) AND semiconductor) OR quantum</code>{" "}
            <span className="text-muted">→ 2,701건 (3중 중첩)</span>
          </li>
          <li>
            <code className="font-mono text-ink">(memory OR flash) AND semiconductor NOT DRAM</code>{" "}
            <span className="text-muted">→ 176건 (그룹 + NOT)</span>
          </li>
        </ul>

        {/* 괄호가 무시되지 않고 실제로 그룹으로 해석된다는 근거 — 같은 단어라도 묶는 위치에 따라
            결과가 19배 차이난다. 검색식을 짤 때 가장 실수하기 쉬운 지점이라 대비 예시로 보여준다. */}
        <p className="mt-3 font-medium text-warning">괄호 위치가 결과를 크게 바꿉니다</p>
        <ul className="mt-1 space-y-0.5">
          <li>
            <code className="font-mono text-ink">(memory OR flash) AND semiconductor</code>{" "}
            <span className="text-muted">→ 208건</span>
          </li>
          <li>
            <code className="font-mono text-ink">memory OR (flash AND semiconductor)</code>{" "}
            <span className="text-muted">→ 3,995건</span>
          </li>
        </ul>
        <p className="mt-1">같은 단어인데 묶는 위치만 달라도 결과가 19배 차이납니다.</p>
      </div>
    );
  }
  return (
    <div id={panelId} className="mt-2 border border-accent-border bg-accent-light p-3 text-xs text-ink-light">
      <p className="font-medium text-ink">KCI 검색식 규칙</p>
      <ul className="mt-2 list-disc space-y-1 pl-4">
        <li>
          <span className="font-medium text-warning">한글 키워드를 쓰세요</span> — 국내 학술지 색인이라 영문
          검색식은 거의 매칭되지 않습니다.
        </li>
        <li>
          비워두면 위 OpenAlex용 검색식이 그대로 쓰입니다. 영문 검색식이 그대로 들어가면 KCI에서 결과가
          0건이 되기 쉽습니다.
        </li>
        <li>
          단순 키워드 검색입니다.{" "}
          <span className="font-medium text-warning">불리언 연산자(AND/OR/NOT) 지원 여부는 확인되지 않았습니다</span> —
          단일 키워드로 간주하세요.
        </li>
        <li>연도를 거르는 API 파라미터가 없어, 응답을 받은 뒤 코드에서 연도로 걸러냅니다.</li>
      </ul>
    </div>
  );
}
