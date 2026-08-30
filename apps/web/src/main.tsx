import React from "react";
import ReactDOM from "react-dom/client";

import { App } from "./app/App";
import "./ui/minimal.css";

const root = document.getElementById("root");
if (!root) throw new Error("Operly frontend root was not found");

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
