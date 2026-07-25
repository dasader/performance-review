import { Suspense, lazy } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";

// FieldList는 즉시 로드(첫 화면), 차트를 쓰는 Report·recharts 번들은 방문 시점에 분리 로드한다.
import FieldList from "./pages/FieldList";
const FieldDetail = lazy(() => import("./pages/FieldDetail"));
const Report = lazy(() => import("./pages/Report"));
const Admin = lazy(() => import("./pages/Admin"));
const FieldReportPage = lazy(() => import("./pages/FieldReportPage"));

export default function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={<p className="p-8 text-sm text-muted">불러오는 중…</p>}>
        <Routes>
          <Route path="/" element={<FieldList />} />
          <Route path="/fields/:fieldId" element={<FieldDetail />} />
          {/* 분야 보고서 전용 페이지 — 통독·PDF 출력용. 분야 화면의 접힌 섹션은 훑어보기용이다. */}
          <Route path="/fields/:fieldId/report/:year" element={<FieldReportPage kind="report" />} />
          <Route
            path="/fields/:fieldId/roadmap-check/:year"
            element={<FieldReportPage kind="roadmap-check" />}
          />
          <Route path="/analyses/:analysisId" element={<Report />} />
          <Route path="/subfields/:subfieldId/:year" element={<Report />} />
          <Route path="/admin" element={<Admin />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  );
}
