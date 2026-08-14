import VoicePanel from "./VoicePanel";

// Top-level home for voice training (moved out of Profile). VoicePanel owns all
// behavior; this wrapper only gives it a page heading.
export default function VoiceView() {
  return (
    <section>
      <div className="view-head">
        <h2>
          Voice
          <span className="meta-inline">
            teach the AI your writing style — samples in, style profile out
          </span>
        </h2>
      </div>
      <VoicePanel />
    </section>
  );
}
