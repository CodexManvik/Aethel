import { useEffect, useState, type KeyboardEvent } from "react";
import { Button } from "../../ui/Button";
import { inputClass } from "../../ui/Field";
import { cn } from "../../ui/cn";
import { FactCard } from "./FactCard";
import { useAddFact, useFacts, type Fact } from "./memoryApi";

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function Group({ title, facts }: { title: string; facts: Fact[] }) {
  if (facts.length === 0) return null;
  return (
    <section className="mt-6" aria-label={title}>
      <h2 className="font-display text-[20px] leading-none text-ink">{title}</h2>
      <div className="mt-3 grid gap-3">{facts.map((f) => <FactCard key={f.id} fact={f} />)}</div>
    </section>
  );
}

export function FactsTab() {
  const [query, setQuery] = useState("");
  const q = useDebounced(query.trim(), 200);
  const { data: facts = [], isSuccess } = useFacts(q);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState("");
  const add = useAddFact();

  const save = () => {
    const text = draft.trim();
    if (text) add.mutate({ scope: "user", text });
    setDraft("");
    setAdding(false);
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") save();
    if (e.key === "Escape") { setDraft(""); setAdding(false); }
  };

  return (
    <div className="py-4">
      <div className="flex flex-wrap items-center gap-3">
        <input
          type="search"
          aria-label="Search facts"
          placeholder="Search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className={inputClass}
        />
        {!adding && <Button onClick={() => setAdding(true)}>Add a fact</Button>}
      </div>
      {adding && (
        <input
          autoFocus
          aria-label="New fact"
          placeholder="e.g. Is vegetarian"
          value={draft}
          maxLength={300}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKey}
          onBlur={() => { if (!draft.trim()) setAdding(false); }}
          className={cn(inputClass, "mt-3 w-full font-voice text-[16px]")}
        />
      )}
      {isSuccess && facts.length === 0 ? (
        <p className="py-6 font-voice text-[15px] text-muted">
          {q
            ? "Nothing matches that."
            : "Nothing remembered yet. When you tell Aethel about yourself, it keeps short notes here, and you can edit or delete any of them."}
        </p>
      ) : (
        <>
          <Group title="About you" facts={facts.filter((f) => f.scope === "user")} />
          <Group title="With Aethel" facts={facts.filter((f) => f.scope.startsWith("persona:"))} />
        </>
      )}
    </div>
  );
}
