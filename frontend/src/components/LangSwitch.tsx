import { setLang, useLang, useT, type Lang } from "../i18n";

const LANGS: Lang[] = ["it", "en"];

export default function LangSwitch() {
  const t = useT();
  const current = useLang();
  return (
    <div className="lang-switch" role="group" aria-label={t("shell.language")}>
      {LANGS.map((code) => (
        <button
          key={code}
          type="button"
          lang={code}
          className={"btn small" + (current === code ? "" : " secondary")}
          aria-pressed={current === code}
          onClick={() => setLang(code)}
        >
          {code.toUpperCase()}
        </button>
      ))}
    </div>
  );
}
