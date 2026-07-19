import { useEffect, useState } from "react";
import { ApiError, del, get, post, put, type AdminSubfield, type Field } from "../api";

interface SubfieldBody {
  field_id: number;
  name: string;
  query: string;
  query_kci: string | null;
  active: boolean;
}

const emptyDraft = { fieldId: "" as number | "", name: "", query: "", queryKci: "" };

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
  const [draft, setDraft] = useState(emptyDraft);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState({ name: "", query: "", queryKci: "" });
  const [editError, setEditError] = useState<string | null>(null);
  const [deleteConflict, setDeleteConflict] = useState<{ id: number; message: string } | null>(null);

  // 검색식 규칙 도움말(i 버튼) 펼침 상태. 신규 추가 폼과 편집 폼(한 번에 하나만 편집됨)을 따로 둔다.
  const [addHelp, setAddHelp] = useState({ openalex: false, kci: false });
  const [editHelp, setEditHelp] = useState({ openalex: false, kci: false });

  const fieldName = (id: number) => fields.find((f) => f.id === id)?.name ?? `분야 #${id}`;

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

  // 편집·비활성화·복구가 모두 "전체 필드를 다시 PUT" 한 형태라 저장 로직을 하나로 묶는다.
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
      setEditError(e instanceof Error ? e.message : "저장에 실패했습니다.");
      return false;
    } finally {
      setBusyId(null);
    }
  };

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    if (draft.fieldId === "") {
      setFormError("분야를 선택하세요.");
      return;
    }
    const query = normalizeQuery(draft.query);
    const queryKci = normalizeQuery(draft.queryKci);
    if (!draft.name.trim() || !query) {
      setFormError("세부기술명과 검색식은 비워둘 수 없습니다.");
      return;
    }
    setSubmitting(true);
    try {
      await post(
        "/admin/subfields",
        {
          field_id: draft.fieldId,
          name: draft.name.trim(),
          query,
          query_kci: queryKci || null,
        },
        adminKey,
      );
      setDraft(emptyDraft);
      await load();
      onChanged();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onUnauthorized();
      setFormError(e instanceof Error ? e.message : "추가에 실패했습니다.");
    } finally {
      setSubmitting(false);
    }
  };

  const startEdit = (item: AdminSubfield) => {
    setEditingId(item.id);
    setEditError(null);
    setEditHelp({ openalex: false, kci: false });
    setEditDraft({ name: item.name, query: item.query, queryKci: item.query_kci ?? "" });
  };

  const handleSaveEdit = async (item: AdminSubfield) => {
    setEditError(null);
    const query = normalizeQuery(editDraft.query);
    const queryKci = normalizeQuery(editDraft.queryKci);
    if (!editDraft.name.trim() || !query) {
      setEditError("세부기술명과 검색식은 비워둘 수 없습니다.");
      return;
    }
    const ok = await saveSubfield(item.id, {
      field_id: item.field_id,
      name: editDraft.name.trim(),
      query,
      query_kci: queryKci || null,
      active: item.active,
    });
    if (ok) setEditingId(null);
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
      setListError(e instanceof Error ? e.message : "삭제에 실패했습니다.");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="border border-border bg-surface p-5">
      <h2 className="font-display text-lg font-semibold text-accent">세부기술 · 검색식</h2>
      <p className="mt-1 text-xs text-muted">
        검색식을 바꾸면 이미 수집된 연도는 다음 실행 상태 표에서 "갱신 필요"로 표시됩니다.
      </p>

      <form onSubmit={handleAdd} className="mt-4 flex flex-col gap-4">
        <div>
          <label htmlFor="new-field-id" className="mb-1 block text-xs font-medium text-ink-light">
            분야
          </label>
          <select
            id="new-field-id"
            value={draft.fieldId}
            onChange={(e) => setDraft({ ...draft, fieldId: e.target.value ? Number(e.target.value) : "" })}
            className="w-full border border-border bg-surface px-3 py-2 text-sm text-ink focus:border-accent sm:max-w-xs"
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
          <label htmlFor="new-name" className="mb-1 block text-xs font-medium text-ink-light">
            세부기술명
          </label>
          <input
            id="new-name"
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            className="w-full border border-border bg-surface px-3 py-2 text-sm text-ink focus:border-accent sm:max-w-sm"
          />
        </div>
        <div>
          <div className="mb-1 flex items-center gap-1.5">
            <label htmlFor="new-query" className="block text-xs font-medium text-ink-light">
              검색식 (OpenAlex)
            </label>
            <QueryHelpToggle
              source="openalex"
              open={addHelp.openalex}
              onToggle={() => setAddHelp((h) => ({ ...h, openalex: !h.openalex }))}
              panelId="query-help-openalex-new"
            />
          </div>
          {addHelp.openalex && <QueryHelpPanel source="openalex" panelId="query-help-openalex-new" />}
          <textarea
            id="new-query"
            ref={autoGrow}
            onInput={(e) => autoGrow(e.currentTarget)}
            rows={3}
            value={draft.query}
            onChange={(e) => setDraft({ ...draft, query: e.target.value })}
            className="mt-1 w-full resize-y border border-border bg-surface px-3 py-2 font-mono text-sm text-ink focus:border-accent"
          />
        </div>
        <div>
          <div className="mb-1 flex items-center gap-1.5">
            <label htmlFor="new-query-kci" className="block text-xs font-medium text-ink-light">
              KCI 검색식 (비우면 공통값 사용)
            </label>
            <QueryHelpToggle
              source="kci"
              open={addHelp.kci}
              onToggle={() => setAddHelp((h) => ({ ...h, kci: !h.kci }))}
              panelId="query-help-kci-new"
            />
          </div>
          {addHelp.kci && <QueryHelpPanel source="kci" panelId="query-help-kci-new" />}
          <textarea
            id="new-query-kci"
            ref={autoGrow}
            onInput={(e) => autoGrow(e.currentTarget)}
            rows={3}
            value={draft.queryKci}
            onChange={(e) => setDraft({ ...draft, queryKci: e.target.value })}
            className="mt-1 w-full resize-y border border-border bg-surface px-3 py-2 font-mono text-sm text-ink focus:border-accent"
          />
        </div>
        <button
          type="submit"
          disabled={submitting}
          className="self-start border border-ink bg-ink px-4 py-2 text-sm font-medium text-paper transition-colors hover:bg-ink/90 disabled:opacity-40"
        >
          {submitting ? "추가 중…" : "추가"}
        </button>
      </form>
      {formError && <p className="mt-2 text-sm text-danger">{formError}</p>}

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
                const isEditing = editingId === item.id;
                const isBusy = busyId === item.id;
                return (
                  <tr key={item.id} className="border-b border-border-light align-top">
                    <td className="py-3 pr-3 text-xs text-muted">{fieldName(item.field_id)}</td>
                    {isEditing ? (
                      <>
                        <td className="py-2 pr-3">
                          <label htmlFor={`edit-name-${item.id}`} className="sr-only">
                            세부기술명
                          </label>
                          <input
                            id={`edit-name-${item.id}`}
                            value={editDraft.name}
                            onChange={(e) => setEditDraft({ ...editDraft, name: e.target.value })}
                            className="w-32 border border-border bg-surface px-2 py-1 text-sm focus:border-accent"
                          />
                        </td>
                        <td className="py-2 pr-3">
                          <div className="mb-1 flex items-center gap-1.5">
                            <label htmlFor={`edit-query-${item.id}`} className="sr-only">
                              검색식
                            </label>
                            <QueryHelpToggle
                              source="openalex"
                              open={editHelp.openalex}
                              onToggle={() => setEditHelp((h) => ({ ...h, openalex: !h.openalex }))}
                              panelId={`query-help-openalex-edit-${item.id}`}
                            />
                          </div>
                          {editHelp.openalex && (
                            <QueryHelpPanel
                              source="openalex"
                              panelId={`query-help-openalex-edit-${item.id}`}
                            />
                          )}
                          <textarea
                            id={`edit-query-${item.id}`}
                            ref={autoGrow}
                            onInput={(e) => autoGrow(e.currentTarget)}
                            rows={2}
                            value={editDraft.query}
                            onChange={(e) => setEditDraft({ ...editDraft, query: e.target.value })}
                            className="mt-1 w-full min-w-40 resize-y border border-border bg-surface px-2 py-1 font-mono text-sm focus:border-accent"
                          />
                        </td>
                        <td className="py-2 pr-3">
                          <div className="mb-1 flex items-center gap-1.5">
                            <label htmlFor={`edit-query-kci-${item.id}`} className="sr-only">
                              KCI 검색식
                            </label>
                            <QueryHelpToggle
                              source="kci"
                              open={editHelp.kci}
                              onToggle={() => setEditHelp((h) => ({ ...h, kci: !h.kci }))}
                              panelId={`query-help-kci-edit-${item.id}`}
                            />
                          </div>
                          {editHelp.kci && (
                            <QueryHelpPanel source="kci" panelId={`query-help-kci-edit-${item.id}`} />
                          )}
                          <textarea
                            id={`edit-query-kci-${item.id}`}
                            ref={autoGrow}
                            onInput={(e) => autoGrow(e.currentTarget)}
                            rows={2}
                            value={editDraft.queryKci}
                            onChange={(e) => setEditDraft({ ...editDraft, queryKci: e.target.value })}
                            placeholder="(공통 사용)"
                            className="mt-1 w-full min-w-40 resize-y border border-border bg-surface px-2 py-1 font-mono text-sm focus:border-accent"
                          />
                        </td>
                      </>
                    ) : (
                      <>
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
                      </>
                    )}
                    <td className="py-3 pr-3">
                      <button
                        type="button"
                        role="switch"
                        aria-checked={item.active}
                        disabled={isBusy}
                        onClick={() => toggleActive(item)}
                        className={`border px-2 py-1 text-xs disabled:opacity-40 ${
                          item.active
                            ? "border-positive/40 text-positive"
                            : "border-border text-faint"
                        }`}
                      >
                        {item.active ? "활성" : "비활성"}
                      </button>
                    </td>
                    <td className="py-3 text-right whitespace-nowrap">
                      {isEditing ? (
                        <>
                          <button
                            type="button"
                            disabled={isBusy}
                            onClick={() => handleSaveEdit(item)}
                            className="mr-2 border border-ink px-2 py-1 text-xs text-ink hover:bg-ink hover:text-paper disabled:opacity-40"
                          >
                            {isBusy ? "저장 중…" : "저장"}
                          </button>
                          <button
                            type="button"
                            onClick={() => setEditingId(null)}
                            className="text-xs text-muted hover:text-ink"
                          >
                            취소
                          </button>
                        </>
                      ) : (
                        <>
                          <button
                            type="button"
                            onClick={() => startEdit(item)}
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
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {editError && <p className="mt-2 text-sm text-danger">{editError}</p>}
        </div>
      )}
      {items && items.length === 0 && (
        <p className="mt-4 text-sm text-muted">등록된 세부기술이 없습니다.</p>
      )}
    </section>
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
