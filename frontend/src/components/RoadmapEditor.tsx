import { useEffect, useState } from "react";
import { get, put, del, type Field, type Roadmap } from "../api";

// 로드맵 원문은 관리자만 보고 쓴다. 공개 조회 API는 점검 결과만 내려주고 원문은
// 내려주지 않는다 — 비공개 판본일 수 있어서다.
export default function RoadmapEditor({
  adminKey,
  fields,
}: {
  adminKey: string;
  fields: Field[];
}) {
  const [fieldId, setFieldId] = useState<number>(fields[0]?.id ?? 0);
  const [roadmap, setRoadmap] = useState<Roadmap | null>(null);
  const [version, setVersion] = useState("");
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    get<Roadmap>(`/admin/fields/${fieldId}/roadmap`, adminKey)
      .then((r) => {
        setRoadmap(r);
        setVersion(r.version_label);
        setContent(r.content_md);
      })
      .catch((e) => setError(e.message));
  };

  useEffect(load, [fieldId, adminKey]);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await put(`/admin/fields/${fieldId}/roadmap`, { version_label: version, content_md: content }, adminKey);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "저장에 실패했습니다.");
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!confirm("등록된 로드맵을 삭제할까요? 이미 생성된 점검 보고서는 남습니다.")) return;
    await del(`/admin/fields/${fieldId}/roadmap`, adminKey);
    setVersion("");
    setContent("");
    load();
  };

  const registered = (roadmap?.goal_count ?? 0) > 0;

  return (
    <section className="mt-6 border border-border bg-surface p-5">
      <h2 className="mb-3 text-lg font-semibold text-accent">전략기술로드맵</h2>

      <label className="block max-w-md text-sm">
        <span className="text-muted">분야</span>
        <select
          value={fieldId}
          onChange={(e) => setFieldId(Number(e.target.value))}
          className="input mt-1"
        >
          {fields.map((f) => (
            <option key={f.id} value={f.id}>
              {f.name}
            </option>
          ))}
        </select>
      </label>

      {roadmap && (
        <p className="mt-3 text-sm text-ink-light">
          {registered
            ? `등록됨 — ${roadmap.version_label} · 목표 ${roadmap.goal_count}개`
            : "등록된 로드맵이 없습니다. 등록하면 이행 점검 보고서를 만들 수 있습니다."}
        </p>
      )}

      <div className="mt-4 space-y-3 border-t border-border pt-4">
        {/* 비공개 판본 여부는 관리자만 판단할 수 있다 — 어디로 나가는지 명시한다.
            임베딩을 로컬화해도 이 문제는 해결되지 않는다(최종 생성이 외부 모델이면
            원문은 프롬프트로 나간다). */}
        <p className="banner banner-warn text-xs">
          ⚠ 여기 저장한 원문은 점검 보고서를 생성할 때 <strong>Gemini API로 전송</strong>됩니다.
          외부로 내보낼 수 없는 판본인지 확인한 뒤 입력하세요.
        </p>

        <label className="block max-w-md text-sm">
          <span className="text-muted">판본</span>
          <input
            value={version}
            onChange={(e) => setVersion(e.target.value)}
            placeholder="2026 제1호 개정"
            className="input mt-1"
          />
        </label>

        <label className="block text-sm">
          <span className="text-muted">
            원문 (마크다운) — 단계별 목표는 <code>| 단계 | 시기 | 기술적 목표 |</code> 형식의
            표로 작성해야 전수 점검이 강제됩니다. 표가 없으면 저장이 거부됩니다.
          </span>
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={18}
            className="mt-1 textarea font-mono text-xs"
          />
        </label>

        {error && <p className="text-sm text-danger">{error}</p>}

        <div className="flex gap-2">
          <button
            type="button"
            onClick={save}
            disabled={saving || !version.trim() || !content.trim()}
            className="btn btn-primary"
          >
            {saving ? "저장 중…" : "저장"}
          </button>
          {registered && (
            <button
              type="button"
              onClick={remove}
              className="btn btn-danger"
            >
              삭제
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
