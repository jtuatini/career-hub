import { useEffect, useState } from "react";
import { api } from "../api";
import { useToast } from "../Toast";

// Field set mirrors the default Greenhouse application form and the Workday
// apply wizard (My Information / My Experience / Application Questions /
// Voluntary Disclosures) — keys must match backend SYNONYMS in
// backend/app/services/autofill.py where a heuristic mapping exists.
const GROUPS: { title: string; hint?: string; fields: [string, string][] }[] = [
  {
    title: "Identity",
    fields: [
      ["full_name", "Full name"],
      ["preferred_name", "Preferred name (if different)"],
      ["middle_name", "Middle name"],
      ["pronouns", "Pronouns"],
      ["email", "Email"],
    ],
  },
  {
    title: "Phone",
    fields: [
      ["phone", "Phone number"],
      ["phone_device_type", "Device type (e.g. Mobile)"],
      ["phone_country_code", "Country phone code (e.g. United States (+1))"],
      ["phone_extension", "Extension"],
    ],
  },
  {
    title: "Location",
    fields: [
      ["address", "Street address"],
      ["address2", "Address line 2 (apt / unit)"],
      ["city", "City"],
      ["state", "State / province"],
      ["zip", "ZIP / postal code"],
      ["country", "Country"],
    ],
  },
  {
    title: "Education",
    fields: [
      ["school", "School"],
      ["degree", "Degree (e.g. Bachelor's Degree)"],
      ["major", "Major / field of study"],
      ["gpa", "GPA"],
      ["edu_start_date", "Education start (e.g. Aug 2024)"],
      ["grad_date", "Expected graduation (e.g. May 2028)"],
    ],
  },
  {
    title: "Links",
    fields: [
      ["linkedin", "LinkedIn URL"],
      ["github", "GitHub URL"],
      ["website", "Personal site / portfolio"],
    ],
  },
  {
    title: "Work authorization",
    fields: [
      ["work_auth", "Authorized to work? (e.g. Yes)"],
      ["sponsorship", "Need sponsorship? (e.g. No)"],
      ["over_18", "18 or older? (e.g. Yes)"],
      ["security_clearance", "Security clearance (e.g. None)"],
    ],
  },
  {
    title: "Logistics & compensation",
    fields: [
      ["start_date", "Earliest start date"],
      ["salary", "Salary expectation"],
      ["relocation", "Willing to relocate? (e.g. Yes)"],
      ["remote_preference", "Remote / hybrid / onsite preference"],
      ["notice_period", "Notice period"],
      ["hear_about", "How did you hear about us? (default answer)"],
      ["referral_name", "Referrer name (if referred)"],
    ],
  },
  {
    title: "Most recent role",
    hint: "Workday's My Experience page asks for these even with a resume attached.",
    fields: [
      ["job_title", "Job title"],
      ["company", "Company"],
      ["job_location", "Company location"],
      ["job_start", "Started (e.g. May 2025)"],
      ["job_end", "Ended (e.g. Aug 2025, or Present)"],
    ],
  },
  {
    title: "Voluntary self-identification",
    hint:
      "Optional EEO questions (Greenhouse EEOC section, Workday Voluntary Disclosures). " +
      "Filled only from what you type here — never guessed — and always flagged yellow " +
      "for review. Leave blank to always answer these yourself.",
    fields: [
      ["gender", "Gender (e.g. Male / Female / Decline To Self Identify)"],
      ["hispanic_latino", "Hispanic or Latino? (e.g. No / Decline To Self Identify)"],
      ["race_ethnicity", "Race / ethnicity (platform wording, e.g. Two or More Races)"],
      ["veteran_status", "Veteran status (e.g. I am not a protected veteran)"],
      ["disability_status", "Disability status (e.g. No, I do not have a disability…)"],
    ],
  },
];

const KNOWN_KEYS = new Set(GROUPS.flatMap((g) => g.fields.map(([k]) => k)));

export default function ProfileView() {
  const toast = useToast();
  const [values, setValues] = useState<Record<string, string>>({});
  const [dirty, setDirty] = useState(false);
  const [savedNote, setSavedNote] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [customKey, setCustomKey] = useState("");
  const [customValue, setCustomValue] = useState("");
  const [token, setToken] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    api.getProfile().then(setValues, (e) => setError(e.message));
  }, []);

  const set = (key: string, value: string) => {
    setValues((v) => ({ ...v, [key]: value }));
    setDirty(true);
    setSavedNote(false);
  };

  const save = async () => {
    try {
      setValues(await api.putProfile(values));
      setDirty(false);
      setSavedNote(true);
      toast("Profile saved");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const customEntries = Object.entries(values).filter(([k]) => !KNOWN_KEYS.has(k));

  return (
    <section>
      <div className="view-head">
        <h2>
          Profile
          <span className="meta-inline">
            powers extension autofill — stays local, sent nowhere
          </span>
        </h2>
        <button className="primary" onClick={save} disabled={!dirty}>
          {savedNote ? "Saved" : "Save profile"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}

      {GROUPS.map((group) => (
        <div className="panel form-panel" key={group.title}>
          <div className="category-head">{group.title}</div>
          {group.hint && <p className="hint">{group.hint}</p>}
          <div className="form-row">
            {group.fields.map(([key, label]) => (
              <label key={key}>
                {label}
                <input value={values[key] ?? ""} onChange={(e) => set(key, e.target.value)} />
              </label>
            ))}
          </div>
        </div>
      ))}

      <div className="panel form-panel">
        <div className="category-head">Custom fields</div>
        {customEntries.map(([key, value]) => (
          <div className="form-row" key={key}>
            <label>
              {key}
              <input value={value} onChange={(e) => set(key, e.target.value)} />
            </label>
          </div>
        ))}
        <div className="form-row">
          <label>
            New field key <span className="optional">e.g. veteran_status</span>
            <input value={customKey} onChange={(e) => setCustomKey(e.target.value)} />
          </label>
          <label>
            Value
            <input value={customValue} onChange={(e) => setCustomValue(e.target.value)} />
          </label>
          <button
            disabled={!customKey.trim() || !customValue.trim()}
            onClick={() => {
              set(customKey.trim(), customValue);
              setCustomKey("");
              setCustomValue("");
            }}
          >
            Add
          </button>
        </div>
        <p className="hint">
          Clear a value and save to remove it. The extension fills matching fields green,
          uncertain ones yellow, and never submits anything.
        </p>
      </div>

      <div className="panel">
        <div className="category-head">Extension access</div>
        <p className="hint">
          The Chrome extension needs this token once (its popup asks for it). It proves a
          caller is yours — random websites and other extensions can no longer reach the API.
        </p>
        {token ? (
          <div className="btn-row">
            <code className="token-value">{token}</code>
            <button
              onClick={() => {
                navigator.clipboard.writeText(token);
                setCopied(true);
              }}
            >
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
        ) : (
          <button onClick={() => api.getExtensionToken().then((t) => setToken(t.token))}>
            Reveal token
          </button>
        )}
      </div>

      <div className="panel">
        <div className="category-head">Backup</div>
        <p className="hint">
          Everything — database, resumes, generated documents — zipped locally. Nothing
          leaves your machine.
        </p>
        <a className="button" href="/api/export">
          Download full backup
        </a>
      </div>
    </section>
  );
}
