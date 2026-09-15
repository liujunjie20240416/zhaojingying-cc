#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────────────
// 把 guide 的 .md 一键转换成模板样式的自包含 HTML，并在浏览器中打开。
//
// 用法：
//   node docs/build-guide.mjs                       # 用默认 md 生成并打开
//   node docs/build-guide.mjs 某文档.md             # 指定其他 md（同目录找模板）
//
// 转换约定（与 guide-html-template.html 顶部的模板说明一致）：
//   - "# Part X 标题"        → <section id="part-x"> + <h2><span class="no">X</span>…
//   - "## / ###"             → h3 / h4
//   - "**Q数字：问题**"      → <ol class="qa"> 卡片，Q 编号由 CSS 计数器按 md 原编号续排
//   - 表格                   → <div class="table-scroll"> 包裹；Part E 的表加 class="weak"
//   - "```mermaid" 围栏      → <pre class="mermaid">，<br/> 写成 &lt;br/&gt;（模板脚本会还原）
//   - 紧跟在标题后的引用块   → <blockquote> 顶部加 <span class="tag">（标题文本，限 18 字内）
// ─────────────────────────────────────────────────────────────────────────────
import { readFileSync, writeFileSync } from "node:fs";
import { spawn } from "node:child_process";
import { dirname, join, basename } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const mdPath = process.argv[2] ?? join(here, "2026-08-15-project-introduction-and-interview-guide.md");
const tplPath = join(here, "guide-html-template.html");
const outPath = join(here, basename(mdPath, ".md") + ".html");

// ── 页面头部信息（按文档改这里即可） ──────────────────────────────────────
const PAGE_TITLE = "DeepEcho（千寻）项目全解与面试指南";
const EYEBROW = "DeepEcho · 千寻";
const SUB = "一份文档完成两件事：① 让你彻底看懂这个项目怎么运转；② 让你能把项目讲给面试官听，并且经得起追问。";
const TIPS = [
  "① 第一遍：Part A + Part B（先建立全链路）",
  "② 第二遍：Part G（补齐 SQLite / FTS5 / LanceDB 原理）",
  "③ 面试前：Part C + Part D + Part H（按追问练习）",
];
const NAV = [
  ["part-a", "A 项目速览"],
  ["part-b", "B 如何运转"],
  ["part-c", "C 设计要点"],
  ["part-d", "D 面试 Q&amp;A"],
  ["part-e", "E 弱点防御"],
  ["part-f", "F 简历模板"],
  ["part-g", "G 原理白话课"],
  ["part-h", "H 面试作战"],
];
const FOOTER = "DeepEcho（千寻）项目全解与面试指南 · 源文档：docs/2026-08-15-project-introduction-and-interview-guide.md";
// ─────────────────────────────────────────────────────────────────────────────

const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function inline(t) {
  let s = esc(t);
  s = s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>');
  return s;
}

// ── 第一步：把 md 切成语义块 ──────────────────────────────────────────────
function parse(raw) {
  const lines = raw.split("\n");
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const hm = line.match(/^(#{1,6})\s+(.*)$/);
    if (hm) { blocks.push({ type: "h" + hm[1].length, text: hm[2] }); i++; continue; }
    if (/^\s*---\s*$/.test(line)) { blocks.push({ type: "hr" }); i++; continue; }
    if (line.trim() === "") { i++; continue; }
    if (line.startsWith("```")) {
      const lang = line.slice(3).trim();
      const buf = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) { buf.push(lines[i]); i++; }
      i++;
      blocks.push({ type: lang === "mermaid" ? "mermaid" : "code", lang, code: buf.join("\n") });
      continue;
    }
    if (line.startsWith("|")) {
      const rows = [];
      while (i < lines.length && lines[i].startsWith("|")) { rows.push(lines[i]); i++; }
      blocks.push({ type: "table", rows });
      continue;
    }
    if (line.startsWith(">")) {
      const buf = [];
      while (i < lines.length && lines[i].startsWith(">")) { buf.push(lines[i].replace(/^>\s?/, "")); i++; }
      blocks.push({ type: "quote", lines: buf });
      continue;
    }
    const isList = (l) => /^(\s*)([-*]|\d+\.)\s+/.test(l);
    const collect = (kind) => {
      const items = [];
      while (i < lines.length) {
        const m = lines[i].match(kind === "ul" ? /^(\s*)[-*]\s+(.*)$/ : /^\s*\d+\.\s+(.*)$/);
        if (m) { items.push(kind === "ol" ? m[1] : m[2]); i++; continue; }
        if (isList(lines[i]) || lines[i].trim() === "" || /^(#{1,6})\s/.test(lines[i]) ||
            lines[i].startsWith("```") || lines[i].startsWith("|") || lines[i].startsWith(">") ||
            /^\s*---\s*$/.test(lines[i])) break;
        items[items.length - 1] += " " + lines[i].trim();
        i++;
      }
      return items;
    };
    if (/^(\s*)[-*]\s+/.test(line)) { blocks.push({ type: "ul", items: collect("ul") }); continue; }
    if (/^\s*\d+\.\s+/.test(line)) { blocks.push({ type: "ol", items: collect("ol") }); continue; }
    // 普通段落：收集到下一个块级起点为止
    const buf = [line.trim()];
    i++;
    while (i < lines.length && lines[i].trim() !== "" && !/^(#{1,6})\s/.test(lines[i]) &&
           !lines[i].startsWith("```") && !lines[i].startsWith("|") && !lines[i].startsWith(">") &&
           !/^\s*---\s*$/.test(lines[i]) && !isList(lines[i])) {
      buf.push(lines[i].trim());
      i++;
    }
    blocks.push({ type: "p", lines: buf });
  }
  return blocks;
}

// ── 第二步：渲染成模板结构的 HTML ─────────────────────────────────────────
function render(blocks) {
  let html = "";
  let section = null;        // { letter, title, id }
  let qaOpen = false;        // <ol class="qa"> 是否打开
  let prev = null;           // 上一个块（判断引用块紧跟标题 → 加 tag）
  let skipToc = false;       // "## 目录" 及其列表整段跳过（导航栏已覆盖）

  const closeQa = () => { if (qaOpen) { html += "</ol>\n"; qaOpen = false; } };

  for (const b of blocks) {
    if (b.type === "hr") { prev = b; continue; }

    if (b.type === "h1") {
      const pm = b.text.match(/^Part ([A-H])\s+(.*)$/);
      if (!pm) { prev = b; continue; } // 文档大标题，跳过（已放 header）
      closeQa();
      if (section) html += "</section>\n";
      section = { letter: pm[1], title: pm[2], id: "part-" + pm[1].toLowerCase() };
      html += `\n<section id="${section.id}">\n<h2><span class="no">${section.letter}</span>${inline(section.title)}</h2>\n`;
      prev = b;
      continue;
    }

    const level = parseInt(b.type[1], 10);
    if (level >= 2 && level <= 6) {
      if (b.type === "h2" && b.text === "目录") { skipToc = true; prev = b; continue; }
      closeQa();
      const tag = "h" + Math.min(level + 1, 6); // h2→h3, h3→h4…
      html += `<${tag}>${inline(b.text)}</${tag}>\n`;
      prev = b;
      continue;
    }

    if (b.type === "ul" || b.type === "ol") {
      if (skipToc && b.type === "ul") { skipToc = false; prev = b; continue; }
      skipToc = false;
      html += `<${b.type}>\n` + b.items.map((it) => `<li>${inline(it)}</li>`).join("\n") + `\n</${b.type}>\n`;
      prev = b;
      continue;
    }

    if (b.type === "table") {
      skipToc = false;
      const rows = b.rows.map((r) => r.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((c) => c.trim()));
      const isSep = (r) => r.every((c) => /^:?-+:?$/.test(c));
      const body = rows.slice(1).filter((r) => !isSep(r));
      const weak = section && section.letter === "E" ? ' class="weak"' : "";
      html += `<div class="table-scroll"><table${weak}>\n<thead><tr>${rows[0].map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead>\n<tbody>\n`;
      for (const r of body) html += `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>\n`;
      html += `</tbody></table></div>\n`;
      prev = b;
      continue;
    }

    if (b.type === "quote") {
      skipToc = false;
      const paras = [];
      let cur = [];
      for (const ln of b.lines) {
        if (ln === "") { if (cur.length) { paras.push(cur.join(" ")); cur = []; } }
        else cur.push(ln);
      }
      if (cur.length) paras.push(cur.join(" "));
      // 紧跟标题的引用块 → 顶部加 tag（限 18 字，不取 Part 字母行）
      let tag = "";
      if (prev && prev.type.startsWith("h") && prev.text && prev.text.length <= 18 &&
          !/^Part [A-H]\s+/.test(prev.text)) tag = prev.text;
      html += "<blockquote>\n";
      if (tag) html += `<span class="tag">${inline(tag)}</span>\n`;
      html += paras.map((p) => `<p>${inline(p)}</p>`).join("\n") + "\n</blockquote>\n";
      prev = b;
      continue;
    }

    if (b.type === "code" || b.type === "mermaid") {
      skipToc = false;
      html += b.type === "mermaid"
        ? `<pre class="mermaid">\n${esc(b.code)}\n</pre>\n`
        : `<pre><code>${esc(b.code)}</code></pre>\n`;
      prev = b;
      continue;
    }

    // 段落：识别 Q&A 卡片（首行是 **Q数字：问题**，其余行是答案，同一块内输出）
    const qm = b.lines[0].match(/^\*\*Q(\d+)[：:]\s*(.+?)\*\*\s*$/);
    if (qm && section && section.letter === "D") {
      if (!qaOpen) html += '<ol class="qa" style="counter-reset: qa ' + (parseInt(qm[1], 10) - 1) + '">\n';
      qaOpen = true;
      const a = b.lines.slice(1).join(" ").replace(/^(参考答法|标准答法)[：:]\s*/, "");
      html += `<li><p class="q">${inline(qm[2])}</p>\n`;
      if (a) html += `<p class="a">${inline(a)}</p>\n`;
      html += `</li>\n`;
    } else {
      html += `<p>${inline(b.lines.join(" "))}</p>\n`;
    }
    prev = b;
  }
  closeQa();
  if (section) html += "</section>\n";
  return html;
}

// ── 第三步：拼进模板 ──────────────────────────────────────────────────────
const tpl = readFileSync(tplPath, "utf8");
const body = render(parse(readFileSync(mdPath, "utf8")));

const out = tpl
  .replace(/<title>.*?<\/title>/, `<title>${PAGE_TITLE}</title>`)
  .replace(/<p class="eyebrow">.*?<\/p>/, `<p class="eyebrow">${EYEBROW}</p>`)
  .replace(/<div class="brand"><h1>.*?<\/h1><\/div>/, `<div class="brand"><h1>${PAGE_TITLE}</h1></div>`)
  .replace(/<p class="sub">.*?<\/p>/, `<p class="sub">${SUB}</p>`)
  .replace(/<div class="reading-tip">[\s\S]*?<\/div>/, `<div class="reading-tip">\n      ${TIPS.map((t) => `<span>${t}</span>`).join("\n      ")}\n    </div>`)
  .replace(/<nav class="toc">[\s\S]*?<\/nav>/, `<nav class="toc">\n  <div class="wrap">\n    ${NAV.map(([id, label]) => `<a href="#${id}">${label}</a>`).join("\n    ")}\n  </div>\n</nav>`)
  .replace(/<main class="wrap">[\s\S]*?<\/main>/, `<main class="wrap">\n${body}</main>`)
  .replace(/<footer class="footer-note">[\s\S]*?<\/footer>/, `<footer class="footer-note">\n  <div class="wrap">${FOOTER}</div>\n</footer>`);

writeFileSync(outPath, out);
console.log("已生成: " + outPath);
spawn("open", [outPath]);
