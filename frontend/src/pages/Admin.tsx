import { useCallback, useEffect, useState } from "react";
import { ApiError, get, post, type DashboardResponse, type Field } from "../api";
import { useAdminKey } from "../useAdminKey";
import TopBar from "../components/TopBar";
import Footer from "../components/Footer";
import SubfieldEditor from "../components/SubfieldEditor";
import SubfieldTab from "../components/SubfieldTab";
import ScheduleSection from "../components/ScheduleSection";
import RoadmapEditor from "../components/RoadmapEditor";
import FieldReportsPanel from "../components/FieldReportsPanel";

// 관리자 화면이 세로로 길어져 스크롤로만 탐색하게 됐다. 작업 단위로 묶는다.
// "분석 실행·상태" · "국가 현황" · "국가 비교"는 전부 세부기술 하나를 다른 축으로
// 보여줄 뿐이라 SubfieldTab 하나로 합쳤다 — 표 하나에서 현황을 보고 셀을 골라 큐잉한다.
const TABS = [
  { id: "subfields", label: "세부기술·검색식" },
  { id: "subfield", label: "세부기술" },
  { id: "schedule", label: "자동 스케줄" },
  { id: "roadmap", label: "전략기술로드맵" },
  { id: "field-reports", label: "분야 보고서" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function Admin() {
  const [tab, setTab] = useState<TabId>("subfields");
  const { key, save, clear } = useAdminKey();
  const [input, setInput] = useState("");
  const [authing, setAuthing] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);

  // 헤더의 "오늘 사용" 예산 표시 전용 — 세부기술 관련 탭은 각자 필요한 데이터를 직접 읽는다.
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [fields, setFields] = useState<Field[] | null>(null);
  const [fieldsError, setFieldsError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 검색식이 바뀌면 확인했던 미리보기 숫자가 더 이상 유효하지 않다. 두 탭이 서로
  // 다른 자리에 있으므로 공통 조상인 여기서 세대를 센다.
  const [subfieldGen, setSubfieldGen] = useState(0);

  const onUnauthorized = useCallback(() => {
    clear();
    setData(null);
    setError("관리자 키가 올바르지 않거나 만료되었습니다. 다시 인증해 주세요.");
  }, [clear]);

  const loadDashboard = useCallback(
    async (adminKey: string) => {
      try {
        setData(await get<DashboardResponse>("/admin/dashboard", adminKey));
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return onUnauthorized();
        setError(e instanceof Error ? e.message : "대시보드를 불러오지 못했습니다.");
      }
    },
    [onUnauthorized],
  );

  useEffect(() => {
    if (key) loadDashboard(key);
  }, [key, loadDashboard]);

  const loadFields = useCallback(() => {
    setFieldsError(null);
    get<Field[]>("/fields")
      .then(setFields)
      .catch((e) => setFieldsError(e instanceof Error ? e.message : "분야 목록을 불러오지 못했습니다."));
  }, []);

  useEffect(() => {
    loadFields();
  }, [loadFields]);

  if (!key) {
    return (
      <div className="min-h-screen">
        <TopBar />
        <main className="mx-auto max-w-sm px-6 py-10">
          <p className="text-eyebrow font-bold uppercase tracking-[0.09em] text-muted">관리자</p>
          <h1 className="mt-2 text-2xl font-bold tracking-tight text-ink">관리자 인증</h1>
          <p className="mt-2 text-sm text-ink-light">
            분석 실행·검색식 편집은 관리자 키가 있어야 접근할 수 있습니다.
          </p>

          <form
            className="mt-6"
            onSubmit={async (e) => {
              e.preventDefault();
              setAuthing(true);
              setAuthError(null);
              try {
                await post("/admin/auth", {}, input);
                save(input);
                setInput("");
              } catch {
                setAuthError("관리자 키가 올바르지 않습니다.");
              } finally {
                setAuthing(false);
              }
            }}
          >
            <label htmlFor="admin-key" className="block text-sm font-medium text-ink-light">
              관리자 키
            </label>
            <input
              id="admin-key"
              type="password"
              autoComplete="off"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              className="mt-2 input"
              required
            />
            <button
              type="submit"
              disabled={authing || !input}
              className="mt-4 btn btn-primary w-full"
            >
              {authing ? "확인 중…" : "접속"}
            </button>
            {authError && <p className="mt-3 text-sm text-danger">{authError}</p>}
          </form>
        </main>
        <Footer />
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <TopBar />
      <main className="mx-auto max-w-page px-6 pb-10 pt-6">
        <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-eyebrow font-bold uppercase tracking-[0.09em] text-muted">관리자</p>
            <h1 className="mt-1 text-2xl font-bold tracking-tight text-ink">분석 운영</h1>
          </div>
          <div className="flex items-center gap-4">
            {data && (
              <p className="text-xs tabular-nums text-muted">
                OpenAlex 오늘 사용 ${data.budget_spent.toFixed(4)} / ${data.budget_limit.toFixed(2)}
              </p>
            )}
            <button
              type="button"
              onClick={clear}
              className="btn btn-neutral btn-sm"
            >
              로그아웃
            </button>
          </div>
        </header>

        {/* role="tablist"를 제대로 쓰려면 화살표 키 이동·aria-controls까지 필요하다.
            여기 필요한 건 화면 전환 토글이라 FieldDetail의 연도 선택과 같이 aria-pressed를 쓴다. */}
        <div className="mb-6 flex flex-wrap gap-2 border-b border-border pb-3" aria-label="관리 메뉴">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              aria-pressed={tab === t.id}
              onClick={() => setTab(t.id)}
className="btn btn-toggle btn-sm"
            >
              {t.label}
            </button>
          ))}
        </div>

        {error && <p className="mb-6 banner banner-risk">{error}</p>}

        {fields === null && !fieldsError && <p className="text-sm text-muted">분야 목록을 불러오는 중…</p>}
        {fieldsError && (
          <p className="mb-6 banner banner-risk">
            {fieldsError}{" "}
            <button type="button" onClick={loadFields} className="ml-1 underline hover:text-danger/80">
              다시 시도
            </button>
          </p>
        )}
        {tab === "subfields" && fields && (
          <SubfieldEditor
            adminKey={key}
            fields={fields}
            onChanged={() => setSubfieldGen((n) => n + 1)}
            onUnauthorized={onUnauthorized}
          />
        )}

        {tab === "subfield" && (
          <SubfieldTab
            adminKey={key}
            onUnauthorized={onUnauthorized}
            subfieldsVersion={subfieldGen}
            onDashboard={setData}
          />
        )}

        {tab === "schedule" && <ScheduleSection adminKey={key} onUnauthorized={onUnauthorized} />}

        {tab === "roadmap" && fields && (
          <RoadmapEditor adminKey={key} fields={fields} onUnauthorized={onUnauthorized} />
        )}

        {tab === "field-reports" && (
          <FieldReportsPanel adminKey={key} onUnauthorized={onUnauthorized} />
        )}
      </main>
      <Footer />
    </div>
  );
}
