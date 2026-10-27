import { ScheduledTask } from "@/lib/types";
import { duration, minutesOf } from "@/lib/format";
import { t } from "@/lib/i18n";

export function Schedule({ tasks, nowMin, onNewTask }: { tasks: ScheduledTask[]; nowMin: number; onNewTask: () => void }) {
  const nextIdx = tasks.findIndex((x) => minutesOf(x.at) > nowMin);
  return (
    <section aria-labelledby="h-pl">
      <div className="sh"><h2 id="h-pl">{t("Programat azi", "Scheduled today")}</h2></div>
      <div className="card">
        {tasks.length === 0 ? (
          <p className="quiet" style={{ margin: 0 }}>{t("Nimic programat azi.", "Nothing scheduled today.")}</p>
        ) : (
          <ol className="sched">
            {tasks.map((task, i) => {
              const cls = minutesOf(task.at) <= nowMin ? "done" : i === nextIdx ? "next" : "";
              return (
                <li key={i} className={cls}>
                  <span className="at">{task.at}</span>
                  <span>
                    <span className="what">{task.message}</span>
                    {i === nextIdx && <span className="in">{t("peste", "in")} {duration((minutesOf(task.at) - nowMin) * 60000)}</span>}
                  </span>
                </li>
              );
            })}
          </ol>
        )}
        <div className="sched-foot"><button className="link" onClick={onNewTask}>{t("Task nou", "New task")}</button></div>
      </div>
    </section>
  );
}
