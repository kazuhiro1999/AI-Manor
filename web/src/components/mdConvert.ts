/* manor web — 小さな Markdown -> HTML 変換（src/manor/board/static/md.js の TS 移植）。
 *
 * 安全の約束: 入力は **必ず先に HTML エスケープしてから** Markdown 記法を組み立てる。
 * ctx モーダルの内容は今日は主人が書いた task.body だが、同じ描画経路を将来 inbox 由来
 * （第三者由来）の文が通る可能性があるため、「Markdown を描画する」が XSS の入口に
 * なってはいけない——エスケープを先に行うことで生の `<script>` 等は無害化され、
 * Markdown の記号（* ` # - | [ ] ( )）は HTML エスケープの対象外なので、その後の
 * タグ組み立てにそのまま使える。リンクの href も http(s):・/・# だけを許し、それ以外
 * （javascript: 等）は "#" に書き換える。
 */

export function mdEscape(s: string | null | undefined): string {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

const MD_INLINE_RE = /`([^`]+)`|\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)\s]+)\)/g;

export function mdInline(s: string | null | undefined): string {
  const text = String(s == null ? "" : s);
  return text.replace(MD_INLINE_RE, (whole, code, bold, linkText, linkUrl) => {
    if (code !== undefined) return "<code>" + code + "</code>";
    if (bold !== undefined) return "<strong>" + bold + "</strong>";
    if (linkText !== undefined) {
      const safe = /^(https?:|\/|#)/i.test(linkUrl) ? linkUrl : "#";
      return `<a href="${safe}" target="_blank" rel="noopener noreferrer">${linkText}</a>`;
    }
    return whole;
  });
}

function mdIsTableSepRow(cells: string[]): boolean {
  if (!cells.length) return false;
  return cells.every((c) => /^:?-{1,}:?$/.test(c.trim()));
}

function mdTableHtml(rawRows: string[]): string {
  const rows = rawRows.map((r) =>
    r
      .trim()
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((c) => c.trim())
  );
  let header: string[] | null = null;
  let body = rows;
  if (rows.length >= 2 && mdIsTableSepRow(rows[1])) {
    header = rows[0];
    body = rows.slice(2);
  }
  let html = '<table class="md-table">';
  if (header) {
    html += "<thead><tr>" + header.map((c) => "<th>" + mdInline(c) + "</th>").join("") + "</tr></thead>";
  }
  html +=
    "<tbody>" +
    body.map((r) => "<tr>" + r.map((c) => "<td>" + mdInline(c) + "</td>").join("") + "</tr>").join("") +
    "</tbody>";
  html += "</table>";
  return html;
}

/** raw（未エスケープ）の Markdown を HTML へ変換する。全体を先にエスケープしてから、
 *  行単位でブロック構造を組む。 */
export function mdToHtml(raw: string | null | undefined): string {
  const escaped = mdEscape(raw);
  const lines = escaped.split(/\r\n|\n/);
  const out: string[] = [];
  let i = 0;
  let listType: "ul" | "ol" | null = null;
  let paragraph: string[] = [];
  let tableRows: string[] | null = null;

  function flushParagraph() {
    if (paragraph.length) {
      out.push("<p>" + mdInline(paragraph.join(" ")) + "</p>");
      paragraph = [];
    }
  }
  function closeList() {
    if (listType) {
      out.push("</" + listType + ">");
      listType = null;
    }
  }
  function closeTable() {
    if (tableRows && tableRows.length) out.push(mdTableHtml(tableRows));
    tableRows = null;
  }

  while (i < lines.length) {
    const line = lines[i];

    if (/^```/.test(line.trim())) {
      flushParagraph();
      closeList();
      closeTable();
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i].trim())) {
        codeLines.push(lines[i]);
        i++;
      }
      i++; // skip closing fence
      out.push("<pre><code>" + codeLines.join("\n") + "</code></pre>");
      continue;
    }

    const heading = line.match(/^(#{1,3})\s+(.*)$/);
    if (heading) {
      flushParagraph();
      closeList();
      closeTable();
      const level = heading[1].length;
      out.push(`<h${level}>` + mdInline(heading[2].trim()) + `</h${level}>`);
      i++;
      continue;
    }

    if (/^\s*\|.*\|\s*$/.test(line)) {
      flushParagraph();
      closeList();
      if (!tableRows) tableRows = [];
      tableRows.push(line);
      i++;
      continue;
    } else if (tableRows) {
      closeTable();
    }

    const ul = line.match(/^\s*[-*]\s+(.*)$/);
    const ol = line.match(/^\s*\d+\.\s+(.*)$/);
    if (ul || ol) {
      flushParagraph();
      const type = ul ? "ul" : "ol";
      if (listType !== type) {
        closeList();
        out.push("<" + type + ">");
        listType = type;
      }
      out.push("<li>" + mdInline((ul || ol)![1].trim()) + "</li>");
      i++;
      continue;
    } else if (listType) {
      closeList();
    }

    if (!line.trim()) {
      flushParagraph();
      i++;
      continue;
    }

    paragraph.push(line.trim());
    i++;
  }
  flushParagraph();
  closeList();
  closeTable();
  return out.join("\n");
}
