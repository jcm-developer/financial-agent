import {
  Fragment,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
  type RefObject,
} from "react";
import { useSearchParams } from "react-router";
import { ArrowDown, MessageSquarePlus, Send, Trash2 } from "lucide-react";

import { useAskChat, useChatThread, useChatThreads, useDeleteChatThread } from "@/api/hooks";
import type { ChatMessage, ChatToolUse } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Markdown } from "@/components/Markdown";
import {
  Alert,
  Button,
  Card,
  CONTROL_CLASSES,
  LinkButton,
  Loading,
  PageTitle,
} from "@/components/pieces";
import { ErrorAlert } from "@/components/Section";
import { chatDay, dateTime, integer, localDayKey, time } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useTitle } from "@/layout/useTitle";
import { useActiveProfile } from "@/profile/useActiveProfile";

/**
 * Questions offered on an empty conversation.
 *
 * They are the kinds of question the chat is for, not a tutorial: why it did
 * what it did, what it would need to see, and how it reads the market now.
 */
const SUGGESTED_QUESTIONS = [
  "¿Cómo ves el mercado hoy?",
  "¿Por qué no has comprado nada en el último ciclo?",
  "¿Qué valor está más cerca de convencerte para comprar, y qué tendría que pasar?",
  "Repasa las posiciones abiertas: ¿mantendrías cada una hoy?",
  "¿Qué titulares te han pesado más en el último ciclo?",
  "¿En qué decisiones del último ciclo tienes menos confianza, y por qué?",
] as const;

const EMPTY_THREADS = "Todavía no has conversado con el modelo de este experimento.";

/** Main's bottom padding (`pb-16`), which the fitted box has to leave free. */
const PAGE_BOTTOM_GAP = 64;

/** Below this the box stops shrinking and the page scrolls instead. */
const MIN_HEIGHT = 360;

/** How far from the bottom still counts as reading the latest message, in px. */
const NEAR_BOTTOM = 120;

/** Tailwind's `lg`, where the thread list moves beside the conversation. */
const WIDE = "(min-width: 64rem)";

/** What each tool is, as the answer's footer names it. */
const TOOL_LABELS: Record<string, string> = {
  search_decisions: "decisiones",
  decision_data: "datos de una decisión",
  positions: "posiciones",
  quotes: "cotizaciones",
  cycles: "ciclos",
  news: "titulares",
};

/**
 * @param tool - One lookup the model made.
 * @return How the footer names it, with the symbol when it had one.
 */
function toolLabel(tool: ChatToolUse): string {
  const label = TOOL_LABELS[tool.name] ?? tool.name;
  const symbol = tool.arguments?.symbol;
  return typeof symbol === "string" && symbol ? `${label} de ${symbol}` : label;
}

/**
 * Fits an element's height to what is left of the window below it.
 *
 * **The conversation scrolls inside its own box and not with the page**, the
 * way every chat does: with the page scrolling, going up through a long answer
 * took the thread list and the question box off the screen with it. CSS alone
 * cannot say "the rest of the viewport" here, because what sits above —header,
 * title, notice— wraps to a different height at every width, so it is measured.
 *
 * @param ref - The element to fit.
 * @param enabled - Whether to fit it; off, the height is left to CSS.
 */
function useFitToViewport(ref: RefObject<HTMLElement | null>, enabled: boolean) {
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    if (!enabled) {
      element.style.height = "";
      return;
    }
    const fit = () => {
      const top = element.getBoundingClientRect().top + window.scrollY;
      const height = window.innerHeight - top - PAGE_BOTTOM_GAP;
      element.style.height = `${Math.max(MIN_HEIGHT, height)}px`;
    };
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, [ref, enabled]);
}

/**
 * Whether a media query matches, following it as the window changes.
 *
 * @param query - The media query.
 * @return Its current value.
 */
function useMediaQuery(query: string) {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const list = window.matchMedia(query);
    const update = () => setMatches(list.matches);
    update();
    list.addEventListener("change", update);
    return () => list.removeEventListener("change", update);
  }, [query]);
  return matches;
}

/**
 * The conversation with the experiment's own model (F9.39).
 *
 * **The thread, the decision and a drafted question all live in the URL**
 * (`?thread=`, `?decision=&symbol=&q=`), for the same reason the profile does:
 * the button in Decisiones is a link, a conversation can be bookmarked, and the
 * back button undoes the step.
 *
 * **The notice under the title is the one piece of help text kept**, because it
 * changes how every answer should be read: the model does not remember why it
 * decided, it rereads its own thesis. Without that line an answer reads as
 * recollection, which it is not.
 *
 * @return The rendered screen.
 */
export function Chat() {
  const { profile, ref } = useActiveProfile();
  useTitle("Conversación", profile?.name);
  const [params, setParams] = useSearchParams();
  const threadId = params.get("thread") ?? undefined;
  const decisionId = params.get("decision") ?? undefined;
  const decisionSymbol = params.get("symbol") ?? undefined;
  const model = profile?.llm_model ?? "el modelo";

  const threads = useChatThreads(ref);
  const [deleting, setDeleting] = useState<{ id: string; title: string } | null>(null);
  const remove = useDeleteChatThread(ref);

  const wide = useMediaQuery(WIDE);
  const grid = useRef<HTMLDivElement>(null);
  // Wide, the list and the conversation share one fitted row; narrow, the list
  // goes on top and only the conversation below it is fitted.
  useFitToViewport(grid, wide);

  function openThread(id: string | undefined) {
    setParams(id ? { thread: id } : {});
  }

  return (
    <>
      <PageTitle>Conversación</PageTitle>
      <p className="mb-6 max-w-3xl text-body-sm text-text-secondary">
        Hablas con {model}, el mismo modelo que decide en este experimento. No recuerda
        por qué decidió: relee sus tesis y los datos que vio. No puede operar ni cambiar
        ajustes, y nada de lo que habléis llega a los ciclos.
      </p>

      <div ref={grid} className="grid gap-6 lg:grid-cols-[16rem_minmax(0,1fr)]">
        <nav
          aria-label="Conversaciones"
          className="flex max-h-56 min-h-0 flex-col gap-3 lg:max-h-none"
        >
          <Button icon={MessageSquarePlus} onClick={() => openThread(undefined)}>
            Nueva conversación
          </Button>
          {threads.isPending && <Loading />}
          {threads.error && <ErrorAlert error={threads.error} />}
          {threads.data?.length === 0 && (
            <p className="text-caption text-text-muted">{EMPTY_THREADS}</p>
          )}
          <ul className="-mr-2 flex min-h-0 flex-col gap-1 overflow-y-auto pr-2">
            {threads.data?.map((thread) => {
              const active = thread.id === threadId;
              return (
                <li key={thread.id} className="flex items-start gap-1">
                  <button
                    type="button"
                    aria-current={active ? "true" : undefined}
                    onClick={() => openThread(thread.id)}
                    className={cn(
                      "min-w-0 flex-1 rounded-md px-3 py-2 text-left transition-colors duration-150",
                      active
                        ? "bg-secondary text-foreground"
                        : "text-text-secondary hover:bg-secondary hover:text-foreground",
                    )}
                  >
                    <span className="block truncate text-body-sm font-medium">
                      {thread.title}
                    </span>
                    <span className="block text-caption text-text-muted">
                      {dateTime(thread.updated_at)}
                    </span>
                  </button>
                  <Button
                    variant="ghost"
                    size="sm"
                    icon={Trash2}
                    aria-label={`Borrar la conversación «${thread.title}»`}
                    title="Borrar la conversación"
                    className="mt-1 px-2 text-error-ink hover:bg-error/8"
                    onClick={() => setDeleting({ id: thread.id, title: thread.title })}
                  />
                </li>
              );
            })}
          </ul>
        </nav>

        <Conversation
          key={threadId ?? "new"}
          profile={ref ?? ""}
          model={model}
          threadId={threadId}
          decisionId={threadId ? undefined : decisionId}
          decisionSymbol={threadId ? undefined : decisionSymbol}
          draft={threadId ? "" : (params.get("q") ?? "")}
          onThread={(id) => setParams({ thread: id })}
          onDropDecision={() => setParams({})}
          fitted={!wide}
        />
      </div>

      <ConfirmDialog
        open={deleting !== null}
        title="Borrar la conversación"
        confirmLabel="Borrar conversación"
        danger
        busy={remove.isPending}
        error={remove.error?.message}
        onCancel={() => setDeleting(null)}
        onConfirm={() => {
          if (!deleting) return;
          remove.mutate(deleting.id, {
            onSuccess: () => {
              if (deleting.id === threadId) openThread(undefined);
              setDeleting(null);
            },
          });
        }}
      >
        Se borra «{deleting?.title}» con todos sus mensajes. Las decisiones y el resto del
        experimento no cambian.
      </ConfirmDialog>
    </>
  );
}

/**
 * One conversation: its messages and the box to ask.
 *
 * It is keyed by the thread, so switching thread starts it clean — the draft,
 * the pending question and the timer belong to the thread they were typed in.
 *
 * @param props - Conversation props.
 * @param props.profile - Profile name.
 * @param props.model - The profile's model, for the waiting line.
 * @param props.threadId - The open thread. Undefined for a new one.
 * @param props.decisionId - The decision a new conversation is about, if any.
 * @param props.decisionSymbol - Its symbol, for the line that says so.
 * @param props.draft - A question to start with, from the URL.
 * @param props.onThread - Called with the id of a thread the first answer created.
 * @param props.onDropDecision - Called when the decision is taken off.
 * @param props.fitted - Whether it fits itself to the window, which it does
 *     when it is not beside the thread list.
 * @return The rendered conversation.
 */
function Conversation({
  profile,
  model,
  threadId,
  decisionId,
  decisionSymbol,
  draft,
  onThread,
  onDropDecision,
  fitted,
}: {
  profile: string;
  model: string;
  threadId: string | undefined;
  decisionId: string | undefined;
  decisionSymbol: string | undefined;
  draft: string;
  onThread: (id: string) => void;
  onDropDecision: () => void;
  fitted: boolean;
}) {
  const thread = useChatThread(threadId);
  const ask = useAskChat(profile);
  const [text, setText] = useState(draft);
  const [pending, setPending] = useState<string | null>(null);
  const elapsed = useElapsed(pending !== null);
  const box = useRef<HTMLElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const [away, setAway] = useState(false);
  // Read inside the effect below without being one of its triggers: scrolling
  // up alone must never make the view jump.
  const awayRef = useRef(false);
  useFitToViewport(box, fitted);

  const messages = thread.data?.messages ?? [];

  const toLatest = useCallback((smooth = false) => {
    const element = scroller.current;
    if (!element) return;
    element.scrollTo({ top: element.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  }, []);

  // A new message pulls the view down only if the reader was already at the
  // bottom, or it is their own question: someone who scrolled up to reread an
  // old answer should not be yanked away from it when the next one lands.
  useLayoutEffect(() => {
    if (!awayRef.current || pending !== null) toLatest();
  }, [messages.length, pending, toLatest]);

  function onScroll() {
    const element = scroller.current;
    if (!element) return;
    const isAway =
      element.scrollHeight - element.scrollTop - element.clientHeight > NEAR_BOTTOM;
    awayRef.current = isAway;
    setAway(isAway);
  }

  function send(question: string) {
    const content = question.trim();
    if (!content || ask.isPending) return;
    setPending(content);
    setText("");
    ask.mutate(
      { content, thread_id: threadId ?? null, decision_id: decisionId ?? null },
      {
        onSuccess: (detail) => {
          if (!threadId) onThread(detail.thread.id);
        },
        onError: () => setText(content),
        onSettled: () => {
          setPending(null);
          // The field was disabled while waiting, which drops its focus.
          window.setTimeout(() => input.current?.focus(), 0);
        },
      },
    );
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    send(text);
  }

  const empty = !threadId && pending === null;

  return (
    <section ref={box} aria-label="Mensajes" className="flex min-h-0 min-w-0 flex-col gap-3">
      <div className="relative min-h-0 flex-1">
        <div
          ref={scroller}
          onScroll={onScroll}
          className="flex h-full min-h-72 flex-col gap-3 overflow-y-auto rounded-md border border-border bg-background p-4"
        >
          {thread.isPending && threadId && <Loading />}
          {thread.error && <ErrorAlert error={thread.error} />}

          {empty && (
            <div>
              <p className="mb-3 text-body-sm text-text-secondary">Puedes empezar por:</p>
              <div className="flex flex-wrap gap-2">
                {SUGGESTED_QUESTIONS.map((question) => (
                  <Button key={question} size="sm" onClick={() => send(question)}>
                    {question}
                  </Button>
                ))}
              </div>
            </div>
          )}

          {messages.map((message, index) => {
            const previous = messages[index - 1];
            const newDay =
              !previous || localDayKey(previous.created_at) !== localDayKey(message.created_at);
            return (
              <Fragment key={message.id}>
                {newDay && <DaySeparator iso={message.created_at} />}
                <Message
                  message={message}
                  profile={profile}
                  // A failed answer offers to ask again the question right above it.
                  onRetry={
                    message.error && previous?.role === "user"
                      ? () => send(previous.content)
                      : undefined
                  }
                />
              </Fragment>
            );
          })}

          {pending !== null && (
            <>
              <UserBubble text={pending} at={new Date().toISOString()} />
              <Loading text={`${model} está pensando… ${elapsed} s`} />
            </>
          )}
          {ask.error && <ErrorAlert error={ask.error} />}
        </div>

        {away && (
          <Button
            size="sm"
            icon={ArrowDown}
            onClick={() => toLatest(true)}
            className="absolute bottom-4 left-1/2 -translate-x-1/2 bg-card shadow-md"
          >
            Ir al último mensaje
          </Button>
        )}
      </div>

      <form onSubmit={onSubmit} className="flex shrink-0 flex-col gap-2">
        {decisionId && (
          <p className="text-caption text-text-secondary">
            Sobre la decisión de {decisionSymbol ?? "este valor"}: el modelo la tendrá delante
            con los datos que vio.{" "}
            <LinkButton variant="subtle" className="text-caption" onClick={onDropDecision}>
              Quitar
            </LinkButton>
          </p>
        )}
        <div className="flex items-center gap-2">
          <input
            ref={input}
            aria-label="Tu pregunta"
            value={text}
            maxLength={4000}
            placeholder="¿Por qué no compraste SAP.DE ayer?"
            onChange={(event) => setText(event.target.value)}
            disabled={ask.isPending}
            className={cn(CONTROL_CLASSES, "min-w-0 flex-1")}
          />
          <Button
            type="submit"
            variant="primary"
            icon={Send}
            className="shrink-0"
            disabled={ask.isPending || !text.trim()}
          >
            Enviar
          </Button>
        </div>
      </form>
    </section>
  );
}

/**
 * Seconds since `running` turned on, ticking once a second.
 *
 * An answer takes 10-30 s, and a waiting line that does not move reads as a
 * hang after the first ten.
 *
 * @param running - Whether to count.
 * @return The whole seconds elapsed, 0 while not running.
 */
function useElapsed(running: boolean) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    setSeconds(0);
    if (!running) return;
    const started = Date.now();
    const timer = window.setInterval(
      () => setSeconds(Math.round((Date.now() - started) / 1000)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [running]);
  return seconds;
}

/**
 * The day chip between messages of different days, as WhatsApp draws it.
 *
 * @param props - Separator props.
 * @param props.iso - When the first message of that day was written.
 * @return The centred chip.
 */
function DaySeparator({ iso }: { iso: string }) {
  return (
    <p className="my-1 self-center rounded-sm bg-card px-3 py-1 text-caption text-text-secondary shadow-sm">
      {chatDay(iso)}
    </p>
  );
}

/**
 * The time a message was written, in its corner.
 *
 * @param props - Stamp props.
 * @param props.iso - When it was written.
 * @return The hour and minute, with the full date as its title.
 */
function Stamp({ iso }: { iso: string }) {
  return (
    <time dateTime={iso} title={dateTime(iso)} className="tabular text-caption text-text-muted">
      {time(iso)}
    </time>
  );
}

/**
 * @param props - Bubble props.
 * @param props.text - What was asked, as typed.
 * @param props.at - When it was asked.
 * @return The question, to the right.
 */
function UserBubble({ text, at }: { text: string; at: string }) {
  return (
    <div className="ml-auto max-w-[85%] rounded-md bg-secondary px-4 py-2.5 text-body-sm">
      <p className="whitespace-pre-wrap">{text}</p>
      <p className="mt-1 text-right">
        <Stamp iso={at} />
      </p>
    </div>
  );
}

/**
 * One message of the thread.
 *
 * An answer carries a footer with its time, what it cost and what the model
 * looked up, so a claim can be traced to the data it came from.
 *
 * @param props - Message props.
 * @param props.message - The message.
 * @param props.profile - Profile name, for the citation links.
 * @param props.onRetry - Asks the question again, offered on a failed answer.
 * @return The rendered message.
 */
function Message({
  message,
  profile,
  onRetry,
}: {
  message: ChatMessage;
  profile: string;
  onRetry?: () => void;
}) {
  if (message.role === "user") {
    return <UserBubble text={message.content} at={message.created_at} />;
  }

  if (message.error) {
    return (
      <Alert className="max-w-[95%] self-start">
        <Stamp iso={message.created_at} /> · No llegó la respuesta: {message.error}
        {onRetry && (
          <>
            {" "}
            <LinkButton onClick={onRetry}>Volver a preguntar</LinkButton>
          </>
        )}
      </Alert>
    );
  }

  const tokens = (message.prompt_tokens ?? 0) + (message.completion_tokens ?? 0);
  const tools = message.tools ?? [];

  return (
    <Card padding="p-4" className="max-w-[95%] self-start">
      <Markdown source={message.content} profile={profile} />
      <p className="mt-3 text-caption text-text-muted">
        <Stamp iso={message.created_at} />
        {" · "}
        {message.llm_model ?? "modelo desconocido"}
        {message.latency_ms ? ` · ${Math.round(message.latency_ms / 1000)} s` : ""}
        {tokens ? ` · ${integer(tokens)} tokens` : ""}
        {tools.length > 0 && ` · consultó ${tools.map(toolLabel).join(", ")}`}
      </p>
    </Card>
  );
}
