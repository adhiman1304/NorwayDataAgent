import { FormEvent, useState } from "react";

type Message = { role: "user" | "assistant"; content: string };

const API_URL = "http://localhost:8000";

export default function App() {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = question.trim();
    if (!trimmed || loading) return;

    setMessages((current) => [...current, { role: "user", content: trimmed }]);
    setQuestion("");
    setError("");
    setLoading(true);
    try {
      const response = await fetch(`${API_URL}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: trimmed, thread_id: "web-session" }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "The agent is unavailable.");
      setMessages((current) => [...current, { role: "assistant", content: payload.answer }]);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "The agent is unavailable.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="shell">
      <header className="masthead">
        <div>
          <p className="kicker">NORWAY / OPEN DATA</p>
          <h1>Ask the public record.</h1>
          <p className="lede">A research desk for Statistics Norway and the Stortinget.</p>
        </div>
        <span className="status"><i /> MCP ONLINE</span>
      </header>
      <section className="workspace">
        <div className="conversation" aria-live="polite">
          {messages.length === 0 ? (
            <div className="empty-state">
              <span className="index">01</span>
              <h2>What should we investigate?</h2>
              <p>Ask for a population trend, an unemployment table, parliamentary parties, or a precise calculation.</p>
            </div>
          ) : messages.map((message, index) => (
            <article className={`message ${message.role}`} key={`${message.role}-${index}`}>
              <span className="message-label">{message.role === "user" ? "YOU" : "AGENT"}</span>
              <p>{message.content}</p>
            </article>
          ))}
          {loading && <div className="message assistant"><span className="message-label">AGENT</span><p className="pulse">Consulting the sources...</p></div>}
        </div>
        <form className="composer" onSubmit={submit}>
          <label htmlFor="question">Your question</label>
          <textarea id="question" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="e.g. How has Norway's population changed since 2015?" rows={3} disabled={loading} />
          <div className="composer-footer">
            <span>Answers cite source IDs and periods when available.</span>
            <button type="submit" disabled={loading || !question.trim()}>{loading ? "Working..." : "Ask agent"}<span aria-hidden="true">↗</span></button>
          </div>
        </form>
        {error && <p className="error" role="alert">{error}</p>}
      </section>
    </main>
  );
}