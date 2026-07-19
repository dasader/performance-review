import { Suspense, lazy } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";

// FieldList는 즉시 로드(첫 화면), 차트를 쓰는 Report·recharts 번들은 방문 시점에 분리 로드한다.
import FieldList from "./pages/FieldList";
const FieldDetail = lazy(() => import("./pages/FieldDetail"));
const Report = lazy(() => import("./pages/Report"));
const Admin = lazy(() => import("./pages/Admin"));

export default function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={<p className="p-8 text-sm text-muted">불러오는 중…</p>}>
        <Routes>
          <Route path="/" element={<FieldList />} />
          <Route path="/fields/:fieldId" element={<FieldDetail />} />
          <Route path="/analyses/:analysisId" element={<Report />} />
          <Route path="/subfields/:subfieldId/:year" element={<Report />} />
          <Route path="/admin" element={<Admin />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  );
}
