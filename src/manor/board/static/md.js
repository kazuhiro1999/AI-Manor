/* manor board -- small Markdown -> HTML converter. No CDN (board-wide policy).
 *
 * Supports: headings #..###, bullet/numbered lists (- and 1.), bold **text**,
 * inline code `code`, fenced code blocks ```, links [text](url), paragraphs,
 * and a minimal pipe table (| a | b |).
 *
 * Safety contract: the input MUST be HTML-escaped before any markdown syntax
 * is turned into tags. ctx modal content is text the user (master) wrote in
 * task.body today, but the same rendering path will later carry text that
 * originates from the inbox (third-party text), so "render markdown" must not
 * become an XSS entry point. Escaping first (mdEscape) neutralizes any raw
 * `<script>` etc.; markdown punctuation (* ` # - | [ ] ( )) is not touched by
 * HTML escaping, so it is still available afterwards to build the real tags.
 * Link hrefs are also restricted to http(s):, /, or # -- anything else
 * (e.g. javascript:) is rewritten to "#".
 */
"use strict";

function mdEscape(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/* Inline markdown (code span / bold / link). `s` is assumed already HTML-escaped.
 * Matched in a single alternation pass so the three forms cannot interfere with
 * each other -- a code span's content is never re-processed by the bold/link
 * rules, and no placeholder substitution (with its own escaping pitfalls) is
 * needed. */
var MD_INLINE_RE = /`([^`]+)`|\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)\s]+)\)/g;

function mdInline(s) {
  var text = String(s == null ? "" : s);
  return text.replace(MD_INLINE_RE, function (whole, code, bold, linkText, linkUrl) {
    if (code !== undefined) return "<code>" + code + "</code>";
    if (bold !== undefined) return "<strong>" + bold + "</strong>";
    if (linkText !== undefined) {
      var safe = /^(https?:|\/|#)/i.test(linkUrl) ? linkUrl : "#";
      return '<a href="' + safe + '" target="_blank" rel="noopener noreferrer">' + linkText + "</a>";
    }
    return whole;
  });
}

function mdIsTableSepRow(cells) {
  if (!cells.length) return false;
  for (var i = 0; i < cells.length; i++) {
    if (!/^:?-{1,}:?$/.test(cells[i].trim())) return false;
  }
  return true;
}

function mdTableHtml(rawRows) {
  var rows = rawRows.map(function (r) {
    return r.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(function (c) {
      return c.trim();
    });
  });
  var header = null;
  var body = rows;
  if (rows.length >= 2 && mdIsTableSepRow(rows[1])) {
    header = rows[0];
    body = rows.slice(2);
  }
  var html = '<table class="md-table">';
  if (header) {
    html += "<thead><tr>" + header.map(function (c) { return "<th>" + mdInline(c) + "</th>"; }).join("") + "</tr></thead>";
  }
  html += "<tbody>" + body.map(function (r) {
    return "<tr>" + r.map(function (c) { return "<td>" + mdInline(c) + "</td>"; }).join("") + "</tr>";
  }).join("") + "</tbody>";
  html += "</table>";
  return html;
}

/* Convert raw (un-escaped) markdown text to HTML. Escapes the whole input
 * first, then builds block-level structure line by line. */
function mdToHtml(raw) {
  var escaped = mdEscape(raw);
  var lines = escaped.split(/\r\n|\n/);
  var out = [];
  var i = 0;
  var listType = null; // "ul" | "ol" | null
  var paragraph = [];
  var tableRows = null;

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
    var line = lines[i];

    if (/^```/.test(line.trim())) {
      flushParagraph();
      closeList();
      closeTable();
      var codeLines = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i].trim())) {
        codeLines.push(lines[i]);
        i++;
      }
      i++; // skip closing fence
      out.push("<pre><code>" + codeLines.join("\n") + "</code></pre>");
      continue;
    }

    var heading = line.match(/^(#{1,3})\s+(.*)$/);
    if (heading) {
      flushParagraph();
      closeList();
      closeTable();
      var level = heading[1].length;
      out.push("<h" + level + ">" + mdInline(heading[2].trim()) + "</h" + level + ">");
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

    var ul = line.match(/^\s*[-*]\s+(.*)$/);
    var ol = line.match(/^\s*\d+\.\s+(.*)$/);
    if (ul || ol) {
      flushParagraph();
      var type = ul ? "ul" : "ol";
      if (listType !== type) {
        closeList();
        out.push("<" + type + ">");
        listType = type;
      }
      out.push("<li>" + mdInline((ul || ol)[1].trim()) + "</li>");
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
