import { Fragment } from "react";
import type { ApprovalPreview as Preview } from "../api";
import { useT } from "../i18n";

/**
 * What the person is about to approve, in their words: who it goes to, the
 * subject, and the start of the text. React escapes all of it, so anything an
 * agent put in the payload is shown as text and never interpreted.
 */
export default function ApprovalPreview({ preview }: { preview: Preview | null }) {
  const t = useT();
  const rows: Array<[string, string, boolean]> = [];

  if (preview?.fields) {
    Object.entries(preview.fields).forEach(([name, value]) => rows.push([name, value, false]));
  } else if (preview) {
    (["to", "cc", "bcc"] as const).forEach((field) => {
      const addresses = preview[field];
      if (addresses?.length) rows.push([t(`appr.${field}`), addresses.join(", "), false]);
    });
    if (preview.subject) rows.push([t("appr.subject"), preview.subject, false]);
    if (preview.body_excerpt) rows.push([t("appr.body"), preview.body_excerpt, true]);
  }

  if (rows.length === 0) return <p className="hint">{t("appr.noPreview")}</p>;

  return (
    <dl className="preview">
      {rows.map(([label, value, multiline]) => (
        <Fragment key={label}>
          <dt>{label}</dt>
          <dd className={multiline ? "text" : undefined}>{value}</dd>
        </Fragment>
      ))}
    </dl>
  );
}
