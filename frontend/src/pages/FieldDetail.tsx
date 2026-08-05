import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  get,
  type Field,
  type FieldReport,
  type FieldSummary,
  type RoadmapCheck,
  type YearRow,
} from "../api";
import TopBar from "../components/TopBar";
import Footer from "../components/Footer";
import StatusBadge from "../components/StatusBadge";
import GeneratedReportSection from "../components/GeneratedReportSection";
import { formatGeneratedAt } from "../lib/format";
import { COUNTRY_NAMES, sortCountries } from "../lib/countries";
import { useAdminKey } from "../useAdminKey";

export default function FieldDetail() {
  const { fieldId } = useParams();
  const [field, setField] = useState<Field | null>(null);
  const [years, setYears] = useState<YearRow[]>([]);
  const [year, setYear] = useState<number | null>(null);
  const [summary, setSummary] = useState<FieldSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 분야 보고서 생성 버튼을 관리자에게만 노출하기 위한 것뿐이다 — 권한 판정 자체는
  // 백엔드(require_admin)가 하고, 여기서 키가 비어 있으면 조회 전용으로 보인다.
  const { key: adminKey } = useAdminKey();

  useEffect(() => {
    get<Field[]>("/fields")
      .then((all) => {
        const found = all.find((f) => f.id === Number(fieldId));
        if (!found) {
          setError("요청하신 분야를 찾을 수 없습니다.");
          return;
        }
        setField(found);
      })
      .catch((e) => setError(e.message));
    get<YearRow[]>(`/fields/${fieldId}/years`)
      .then((rows) => {
        setYears(rows);
        setYear(rows[0]?.year ?? null);
      })
      .catch((e) => setError(e.message));
  }, [fieldId]);

  useEffect(() => {
    if (year == null) return;
    // 연도를 바꾸면 이전 연도 표를 즉시 지운다 — 남겨두면 위 제목은 새 연도인데 아래
    // 표는 옛 연도인 상태가 응답이 올 때까지 보인다. stale 플래그는 탭을 빠르게 여러 번
    // 눌렀을 때 응답이 뒤바뀌어 도착해 엉뚱한 연도가 남는 것을 막는다.
    setSummary(null);
    let stale = false;
    get<FieldSummary>(`/fields/${fieldId}/summary?year=${year}`)
      .then((s) => !stale && setSummary(s))
      .catch((e) => !stale && setError(e.message));
    return () => {
      stale = true;
    };
  }, [fieldId, year]);

  if (error) {
    return (
      <div className="min-h-screen">
        <TopBar />
        <main className="mx-auto max-w-page px-6 pb-10 pt-6">
          <p className="text-sm text-danger">{error}</p>
          <Link to="/" className="mt-4 inline-block text-sm text-muted hover:text-ink">
            ← 분야 목록으로 돌아가기
          </Link>
        </main>
        <Footer />
      </div>
    );
  }
  if (!field) return <p className="p-6 text-sm text-muted">불러오는 중…</p>;

  return (
    <div className="min-h-screen">
      <TopBar />
      <main className="mx-auto max-w-page px-6 pb-10 pt-6">
        <Link to="/" className="text-sm text-muted hover:text-ink">
          ← 분야 목록
        </Link>
        <h1 className="mt-1 text-3xl font-extrabold tracking-tight text-ink">{field.name}</h1>

        {years.length === 0 ? (
          <p className="mt-6 text-sm text-muted">아직 분석된 결과가 없습니다.</p>
        ) : (
          <>
            {/* role="tab"을 제대로 쓰려면 aria-controls·tabpanel·화살표 키 이동까지 필요한데,
                여기 실제로 필요한 건 "연도 필터 토글"이다 — 반쪽짜리 tablist보다 aria-pressed가
                동작과 맞고 추가로 구현할 것도 없다. */}
            <div className="mt-4 flex flex-wrap gap-2" aria-label="연도 선택">
              {years.map((y) => (
                <button
                  key={y.year}
                  type="button"
                  aria-pressed={y.year === year}
                  onClick={() => setYear(y.year)}
                  className="btn btn-toggle btn-sm tabular-nums"
                >
                  {y.year}
                  <span className="ml-2 opacity-70">
                    ({y.done_count}/{y.subfield_count})
                  </span>
                </button>
              ))}
            </div>

            {year != null && (
              <>
                <GeneratedReportSection<FieldReport>
                  title="분야 종합 보고서"
                  path={`fields/${fieldId}/report?year=${year}`}
                  viewPath={`/fields/${fieldId}/report/${year}`}
                  adminKey={adminKey}
                  emptyText="아직 생성되지 않았습니다. 완성된 세부기술 보고서를 합성해 분야 전체의 기술적 진전을 정리합니다."
                  meta={(r) =>
                    `세부기술 보고서 ${r.source_count}건 기준 · ${formatGeneratedAt(r.generated_at)} 생성`
                  }
                  staleText={(r) =>
                    `생성 이후 완성된 세부기술 보고서가 ${r.current_count}건으로 늘었습니다. 최신 내용을 반영하려면 다시 생성하세요.`
                  }
                />

                <GeneratedReportSection<RoadmapCheck>
                  title="로드맵 이행 점검"
                  path={`fields/${fieldId}/roadmap-check?year=${year}`}
                  viewPath={`/fields/${fieldId}/roadmap-check/${year}`}
                  adminKey={adminKey}
                  emptyText="아직 생성되지 않았습니다. 로드맵을 등록하면 단계별 목표별로 관련 연구가 확인되는지 전수 점검합니다."
                  buildNote="⚠ 생성 시 로드맵 원문이 Gemini API로 전송됩니다."
                  meta={(r) => (
                    <>
                      로드맵 {r.roadmap_version} · 목표 {r.goal_count}개 · 세부기술 보고서{" "}
                      {r.source_count}건 기준 · {formatGeneratedAt(r.generated_at)} 생성
                      {/* 전수 점검이 깨진 채 저장된 보고서는 "빠짐없이 봤다"로 읽히면 안 된다. */}
                      {r.incomplete && (
                        <span className="ml-2 text-danger">
                          목표 {r.goal_count}개 중 {r.checked_count}개만 점검됨 — 다시 생성하세요
                        </span>
                      )}
                    </>
                  )}
                  staleText={(r) =>
                    r.current_count !== r.source_count
                      ? `생성 이후 완성된 세부기술 보고서가 ${r.current_count}건으로 늘었습니다. 다시 생성하세요.`
                      : "로드맵 판본이 바뀌었습니다. 다시 생성하세요."
                  }
                />
              </>
            )}

            {summary && (
              <>
                {/* 이 숫자는 아래 표의 합계다. 보고서 카드와 표 사이에 홀로 떠 있으면
                    무엇의 합계인지 드러나지 않아, 표의 머리말로 붙여 소유자를 만든다. */}
                <div className="mt-10 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-border pb-2">
                  <h2 className="text-xl font-bold tracking-tight text-ink">세부기술별 분석 현황</h2>
                  <p className="text-sm text-ink-light">
                    {summary.year}년 합계 · 검색{" "}
                    <span className="tabular-nums">{summary.total_searched.toLocaleString()}</span>건
                    / 분석 대상{" "}
                    <span className="tabular-nums">{summary.total_analyzed.toLocaleString()}</span>건
                  </p>
                </div>
                {/* 두 수가 다른 이유를 여기서 밝힌다 — 표는 두 건수를 나란히 놓기만 한다. */}
                <p className="mt-1 text-xs text-muted">
                  <strong className="font-medium text-ink-light">분석 대상</strong>은 검색된 논문에서
                  초록 미보유 등의 사유로 제외하고 남아 실제 성과 추출에 사용된 논문 수입니다.
                </p>

                {/* 좁아지면 셀을 눌러 담지 않고 가로로 스크롤한다 — 셀이 세로로 쪼개지면
                    열 정렬이 무너져 숫자 비교가 불가능해진다. */}
                <div className="mt-4 table-scroll">
                  {/* 뒤 세 열에 최소 폭을 준다. 주지 않으면 열 너비가 그 해의 내용으로만
                      정해져(table-layout: auto) 연도를 바꿀 때 표가 통째로 재배치된다 —
                      실측: 보고서 열이 2025년엔 버튼 4개(중국·일본·한국·미국), 2026년엔
                      1개라 앞 열들이 좌우로 밀렸다(사용자 지적). 연도 토글은 "같은 것을
                      시점만 바꿔 보는" 조작이라 그때 열이 움직이면 눈이 열을 다시 찾는다.

                      table-fixed가 아니라 min-w를 쓰는 이유: 상태 라벨이 최대
                      "일시중지 (예산 소진)"까지 가고 국가도 더 늘 수 있어, 넘칠 때는
                      잘리지 않고 늘어나는 편이 안전하다. 최소 폭만 정하면 흔한 경우가
                      고정되고 드문 경우는 그래도 읽힌다.

                      min-w-[640px]: 뒤 세 열 최소(104+112+200=416)에 세부기술명이
                      두 줄로 접히지 않을 정도(약 224px)를 더한 값. 좁은 화면에서는
                      아래 주석대로 눌러 담지 않고 가로로 스크롤한다. */}
                  <table className="w-full min-w-[640px] border-collapse text-sm">
                    <thead className="tbl-head">
                      <tr className="border-b border-border">
                        <th>세부기술</th>
                        <th className="min-w-[104px]">상태</th>
                        <th className="n min-w-[112px]">검색 / 분석</th>
                        <th className="min-w-[200px]">보고서</th>
                      </tr>
                    </thead>
                    <tbody>
                      {summary.subfields.map((s) => (
                        <tr key={s.subfield_id} className="border-b border-border-light">
                          <td className="px-3 py-2 font-medium text-ink">
                            {s.analysis_id ? (
                              <Link
                                to={`/subfields/${s.subfield_id}/${year}`}
                                className="hover:underline"
                              >
                                {s.subfield_name}
                              </Link>
                            ) : (
                              s.subfield_name
                            )}
                          </td>
                          <td className="px-3 py-2">
                            <StatusBadge status={s.status} label={s.status_label} />
                          </td>
                          {/* 0건은 "아직 검색을 안 돌렸다"는 뜻이라 결측 기호로 쓴다 —
                              0을 그대로 쓰면 "검색했는데 없었다"와 구별되지 않는다. */}
                          <td className="whitespace-nowrap px-3 py-2 text-right text-xs tabular-nums text-muted">
                            {s.searched_count > 0
                              ? `${s.searched_count.toLocaleString()} / ${s.analyzed_count.toLocaleString()}`
                              : "—"}
                          </td>
                          <td className="px-3 py-2">
                            {s.countries.length === 0 ? (
                              <span className="text-faint">—</span>
                            ) : (
                              <span className="flex flex-wrap gap-1">
                                {sortCountries(s.countries).map((c) => (
                                  <Link
                                    key={c}
                                    to={`/subfields/${s.subfield_id}/${year}?country=${c}`}
                                    className="btn btn-toggle btn-sm"
                                  >
                                    {COUNTRY_NAMES[c] ?? c}
                                  </Link>
                                ))}
                              </span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </>
        )}
      </main>
      <Footer />
    </div>
  );
}
