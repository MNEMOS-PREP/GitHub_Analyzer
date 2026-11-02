/** M03 running alone on :5203: the same routes the shell mounts, with the providers the shell would own. */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SEEDED_CANDIDATE_ID, ensureSession } from "@ai/core";

import { M03_HOME, m03Routes } from "./routes";
import "./ui/theme.css";

ensureSession({ role: "student", subject: SEEDED_CANDIDATE_ID });

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <div className="mx-auto max-w-4xl px-4 py-8">
          <Routes>
            <Route index element={<Navigate to={M03_HOME} replace />} />
            {m03Routes}
            <Route path="*" element={<Navigate to={M03_HOME} replace />} />
          </Routes>
        </div>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
