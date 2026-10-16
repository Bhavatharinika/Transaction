import { Link, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { OverviewPage } from "./pages/OverviewPage";
import { SystemPage } from "./pages/SystemPage";
import { TransactionDetailPage } from "./pages/TransactionDetailPage";
import { TransactionsPage } from "./pages/TransactionsPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<OverviewPage />} />
        <Route path="transactions" element={<TransactionsPage />} />
        <Route path="transactions/:id" element={<TransactionDetailPage />} />
        <Route path="system" element={<SystemPage />} />
        <Route path="*" element={<div className="card p-8"><p className="font-medium">Page not found</p><Link className="mt-2 inline-block text-sm text-brand hover:underline" to="/">Back to overview</Link></div>} />
      </Route>
    </Routes>
  );
}
