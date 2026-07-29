import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ApiError, get, post } from "../api";

// 분야 종합 보고서와 로드맵 이행 점검이 공유하는 껍데기. 조회(404=미생성) → 생성 →
// 재조회 → 접기/펼치기 흐름이 완전히 같고, 다른 것은 제목과 메타 표시뿐이라 그
// 부분만 render prop으로 뺐다.
//
// 세부기술 보고서(Report)와 달리 각주·참고문헌이 없다 — 두 보고서 모두 합성 입력이
// 이미 완성된 보고서라 논문을 개별 인용하지 않는다. 그래서 citeAnchors 처리 없이
// 마크다운을 그대로 렌더한다.
export interface GeneratedReport {
  // pending: 큐잉됨(잡 루프가 처리 예정) | done | failed. "생성"은 즉시 실행이 아니라
  // 큐잉일 뿐이라, 화면은 그 자리에 머물며 status를 폴링해 done될 때 갱신한다.
  status: "pending" | "done" | "failed";
  error: string | null;
  report_md: string;
  stale: boolean;
  generated_at: string | null;
}

export default function GeneratedReportSection<T extends GeneratedReport>({
  title,
  path,
  adminKey,
  emptyText,
  viewPath,
  buildNote,
  meta,
  staleText,
}: {
  title: string;
  /** 조회는 `/{path}`, 생성은 `/admin/{path}` — 두 기능 모두 이 규칙을 따른다. */
  path: string;
  adminKey: string;
  emptyText: string;
  /** 전용 페이지 경로 — 통독·PDF 출력은 거기서 한다. */
  viewPath: string;
  /** 생성 버튼 아래에 늘 띄울 주의 문구(예: 원문이 외부 API로 전송된다는 고지). */
  buildNote?: ReactNode;
  meta: (report: T) => ReactNode;
  staleText: (report: T) => string;
}) {
  const [report, setReport] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [queuing, setQueuing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // path가 바뀌면(분야·연도 변경) 이전 결과를 즉시 지운다 — 남겨두면 제목은 새
    // 연도인데 본문은 옛 연도인 상태가 응답이 올 때까지 보인다. stale 플래그는 연도를
    // 빠르게 여러 번 눌렀을 때 응답이 뒤바뀌어 도착해 엉뚱한 결과가 남는 것을 막는다.
    setReport(null);
    setError(null);
    setLoading(true);
    let stale = false;
    get<T>(`/${path}`)
      .then((r) => !stale && setReport(r))
      // 404는 "아직 생성 안 됨"이라는 정상 상태다 — 에러로 표시하지 않는다.
      .catch((e) => {
        if (stale) return;
        if (!(e instanceof ApiError && e.status === 404)) setError(e.message);
      })
      .finally(() => !stale && setLoading(false));
    return () => {
      stale = true;
    };
  }, [path]);

  // 큐잉된 보고서를 폴링한다. status가 pending인 동안만 돈다 — 잡 루프가 30초 틱에
  // 하나씩 처리하므로 4초 간격이면 완료 직후 화면이 곧 갱신된다. done/failed가 되면
  // status 의존성이 바뀌며 이 effect가 정리되어 폴링이 멈춘다.
  const isPending = report?.status === "pending";
  useEffect(() => {
    if (!isPending) return;
    let stopped = false;
    const timer = setInterval(async () => {
      try {
        const fresh = await get<T>(`/${path}`);
        if (!stopped) setReport(fresh);
      } catch {
        /* 일시 오류는 무시하고 다음 틱에 재시도 */
      }
    }, 4000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [isPending, path]);

  const build = async () => {
    // "생성"은 즉시 실행이 아니라 큐잉이다 — 클릭해도 화면은 이 자리에 머물고,
    // 잡 루프가 처리하는 동안 위 폴링이 완료를 감지해 갱신한다. done 상태를 다시
    // 만드는 것만 확인을 받는다(기존 보고서를 덮어쓰고 LLM 비용이 발생하므로).
    if (
      report?.status === "done" &&
      !confirm(
        `${title} 보고서를 다시 생성할까요?\n\n` +
          "기존 보고서를 덮어쓰며 되돌릴 수 없습니다. 큐에 등록되어 순서가 되면 생성되고, LLM 호출 비용이 발생합니다.",
      )
    ) {
      return;
    }
    setQueuing(true);
    setError(null);
    try {
      await post(`/admin/${path}`, null, adminKey);
      // 큐잉 직후 상태를 다시 읽으면 status=pending — 위 폴링이 이어받는다.
      setReport(await get<T>(`/${path}`));
    } catch (e) {
      setError(e instanceof Error ? e.message : "보고서 생성 요청에 실패했습니다.");
    } finally {
      setQueuing(false);
    }
  };

  if (loading) return null;
  // 보고서도 없고 관리자도 아니면 이 영역 자체를 숨긴다 — 일반 방문자에게
  // "없음"만 알리는 빈 상자를 보여줄 이유가 없다.
  if (!report && !adminKey) return null;

  const isDone = report?.status === "done";
  const isFailed = report?.status === "failed";
  const buttonLabel = queuing
    ? "요청 중…"
    : isPending
      ? "생성 중…"
      : report
        ? "다시 생성"
        : "생성";

  return (
    <section className="mt-6 border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-xl font-bold tracking-tight text-ink">{title}</h2>
        <div className="flex shrink-0 items-center gap-2">
          {adminKey && (
            <button
              type="button"
              onClick={build}
              disabled={queuing || isPending}
              className="btn btn-neutral btn-sm"
            >
              {buttonLabel}
            </button>
          )}
          {/* 완성된 보고서만 "보기"를 연다 — pending 중엔 본문이 비었거나 옛 판이다. */}
          {isDone && (
            <Link to={viewPath} className="btn btn-secondary btn-sm">
              보기
            </Link>
          )}
        </div>
      </div>

      {adminKey && buildNote && <p className="mt-2 text-xs text-muted">{buildNote}</p>}
      {error && <p className="mt-3 text-sm text-danger">{error}</p>}

      {isPending && (
        <p className="mt-3 text-sm text-muted">
          생성 대기 중입니다. 잡 루프가 순서대로 처리하며, 완료되면 이 화면이 자동으로 갱신됩니다.
        </p>
      )}
      {isFailed && (
        <p className="mt-3 text-sm text-danger">
          생성 실패: {report?.error ?? "알 수 없는 오류"} — 다시 생성해 보세요.
        </p>
      )}

      {!report && !isPending && <p className="mt-3 text-sm text-muted">{emptyText}</p>}

      {isDone && report && (
        <>
          <div className="mt-2 text-xs text-muted">{meta(report)}</div>
          {report.stale && <p className="mt-2 text-sm text-danger">{staleText(report)}</p>}
        </>
      )}
    </section>
  );
}
