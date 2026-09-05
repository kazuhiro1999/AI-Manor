import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter } from "react-router-dom";
import { App } from "./app/App";
import "./styles/main.css";

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("#root が見つかりません");

ReactDOM.createRoot(rootEl).render(
  <React.StrictMode>
    <HashRouter>
      <App />
    </HashRouter>
  </React.StrictMode>
);
