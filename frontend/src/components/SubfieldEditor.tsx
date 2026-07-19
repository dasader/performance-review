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
    if (!draft.name.trim() || !draft.query.trim()) {
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
          query: draft.query.trim(),
          query_kci: draft.queryKci.trim() || null,
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
    setEditDraft({ name: item.name, query: item.query, queryKci: item.query_kci ?? "" });
  };

  const handleSaveEdit = async (item: AdminSubfield) => {
    setEditError(null);
    if (!editDraft.name.trim() || !editDraft.query.trim()) {
      setEditError("세부기술명과 검색식은 비워둘 수 없습니다.");
      return;
    }
    const ok = await saveSubfield(item.id, {
      field_id: item.field_id,
      name: editDraft.name.trim(),
      query: editDraft.query.trim(),
      query_kci: editDraft.queryKci.trim() || null,
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
      <h2 className="font-display text-lg font-semibold text-ink">세부기술 · 검색식</h2>
      <p className="mt-1 text-xs text-muted">
        검색식을 바꾸면 이미 수집된 연도는 다음 실행 상태 표에서 "갱신 필요"로 표시됩니다.
      </p>

      <form onSubmit={handleAdd} className="mt-4 flex flex-wrap items-end gap-2">
        <div>
          <label htmlFor="new-field-id" className="mb-1 block text-xs font-medium text-ink-light">
            분야
          </label>
          <select
            id="new-field-id"
            value={draft.fieldId}
            onChange={(e) => setDraft({ ...draft, fieldId: e.target.value ? Number(e.target.value) : "" })}
            className="border border-border bg-surface px-3 py-2 text-sm text-ink focus:border-accent"
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
            className="w-40 border border-border bg-surface px-3 py-2 text-sm text-ink focus:border-accent"
          />
        </div>
        <div className="min-w-48 flex-1">
          <label htmlFor="new-query" className="mb-1 block text-xs font-medium text-ink-light">
            검색식 (OpenAlex)
          </label>
          <input
            id="new-query"
            value={draft.query}
            onChange={(e) => setDraft({ ...draft, query: e.target.value })}
            className="w-full border border-border bg-surface px-3 py-2 text-sm text-ink focus:border-accent"
          />
        </div>
        <div className="min-w-48 flex-1">
          <label htmlFor="new-query-kci" className="mb-1 block text-xs font-medium text-ink-light">
            KCI 검색식 (비우면 공통값 사용)
          </label>
          <input
            id="new-query-kci"
            value={draft.queryKci}
            onChange={(e) => setDraft({ ...draft, queryKci: e.target.value })}
            className="w-full border border-border bg-surface px-3 py-2 text-sm text-ink focus:border-accent"
          />
        </div>
        <button
          type="submit"
          disabled={submitting}
          className="border border-ink bg-ink px-4 py-2 text-sm font-medium text-paper transition-colors hover:bg-ink/90 disabled:opacity-40"
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
                <th className="py-2 pr-3 font-medium">검색식</th>
                <th className="py-2 pr-3 font-medium">KCI 검색식</th>
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
                          <label htmlFor={`edit-query-${item.id}`} className="sr-only">
                            검색식
                          </label>
                          <input
                            id={`edit-query-${item.id}`}
                            value={editDraft.query}
                            onChange={(e) => setEditDraft({ ...editDraft, query: e.target.value })}
                            className="w-full min-w-40 border border-border bg-surface px-2 py-1 text-sm focus:border-accent"
                          />
                        </td>
                        <td className="py-2 pr-3">
                          <label htmlFor={`edit-query-kci-${item.id}`} className="sr-only">
                            KCI 검색식
                          </label>
                          <input
                            id={`edit-query-kci-${item.id}`}
                            value={editDraft.queryKci}
                            onChange={(e) => setEditDraft({ ...editDraft, queryKci: e.target.value })}
                            placeholder="(공통 사용)"
                            className="w-full min-w-40 border border-border bg-surface px-2 py-1 text-sm focus:border-accent"
                          />
                        </td>
                      </>
                    ) : (
                      <>
                        <td className="py-3 pr-3 font-medium text-ink">{item.name}</td>
                        <td className="py-3 pr-3 text-ink-light">{item.query}</td>
                        <td className="py-3 pr-3 text-faint">{item.query_kci ?? "(공통 사용)"}</td>
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
