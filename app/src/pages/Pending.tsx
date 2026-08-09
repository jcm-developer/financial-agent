import { Card, PageTitle } from "@/components/pieces";

/**
 * The gap left by a screen that arrives in stretch D, saying **which** task
 * brings it.
 *
 * A bare "coming soon" is the kind of sign that survives for months because
 * nobody knows what is missing. By naming the task, the screen becomes its own
 * to-do list, and whoever opens it during the experiment knows whether they are
 * looking at a gap or at a breakage.
 *
 * @param props - Placeholder props.
 * @param props.title - Heading of the screen that does not exist yet.
 * @param props.task - Task id in TASKS.md that will bring it.
 * @param props.description - What the screen will answer once it is there.
 * @return The rendered placeholder.
 */
export function Pending({
  title,
  task,
  description,
}: {
  title: string;
  task: string;
  description: string;
}) {
  return (
    <>
      <PageTitle>{title}</PageTitle>
      <Card dashed padding="p-6">
        <p className="text-[13px] text-text-secondary">{description}</p>
        <p className="mt-3 text-[13px] text-text-muted">
          Pendiente de <span className="font-semibold text-foreground">{task}</span>. Los
          datos ya están publicados en la API; falta la pantalla.
        </p>
      </Card>
    </>
  );
}
