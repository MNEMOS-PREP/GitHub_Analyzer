/** M03 - GitHub Analyzer · public surface. Anything not exported here is private to the module. */

export { M03_HOME, m03Routes } from "./routes";
export { RepoPage } from "./pages/RepoPage";
export { AnalysisPage } from "./pages/AnalysisPage";
export { ProfilesPage } from "./pages/ProfilesPage";
export { useRepo, useSubmitRepo } from "./api";
export type { RepoView, Question, Decision, ProfilesView, ProfileQuestion } from "./types";
