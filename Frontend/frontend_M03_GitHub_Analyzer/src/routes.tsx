/**
 * M03 - GitHub Analyzer · routes.
 *
 *   /github          paste a repository link; earlier analyses
 *   /github/profiles Codeforces, LeetCode and CodeChef profiles, and the questions they make
 *   /github/:repoId  what was found, and the questions it makes
 *
 * No index route: the host (the shell's Today, or standalone's redirect) owns it.
 */

import { Route } from "react-router-dom";

import { AnalysisPage } from "./pages/AnalysisPage";
import { ProfilesPage } from "./pages/ProfilesPage";
import { RepoPage } from "./pages/RepoPage";

/** Where this module's front door is, standalone and in the shell alike. */
export const M03_HOME = "/github";

export const m03Routes = (
  <Route key="m03">
    <Route path="/github" element={<RepoPage />} />
    <Route path="/github/profiles" element={<ProfilesPage />} />
    <Route path="/github/:repoId" element={<AnalysisPage />} />
  </Route>
);
