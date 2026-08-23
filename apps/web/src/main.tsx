import React from "react";
import ReactDOM from "react-dom/client";

import { App } from "./app/App";
import "./ui/tokens.css";
import "./ui/app.css";
import "./ui/members.css";
import "./ui/messages.css";
import "./ui/settings.css";
import "./ui/security.css";
import "./ui/premium.css";

const root = document.getElementById("root");
if (!root) throw new Error("Operly frontend root was not found");

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
