import { ScheduledTask } from "@/lib/types";
import { duration, minutesOf } from "@/lib/format";

export function Schedule({ tasks, nowMin, onNewTask }: { tasks: ScheduledTask[]; nowMin: number; onNewTask: () => void }) {
  const nextIdx = tasks.findIndex((t) => minutesOf(t.at) > nowMin);
  return (
    <section aria-labelledby="h-pl">
      <div className="sh"><h2 id="h-pl">Programat azi</h2></div>
      <div className="card">
        {tasks.length === 0 ? (
          <p className="quiet" style={{ margin: 0 }}>Nimic programat azi.</p>
        ) : (
          <ol className="sched">
            {tasks.map((t, i) => {
              const cls = minutesOf(t.at) <= nowMin ? "done" : i === nextIdx ? "next" : "";
              return (
                <li key={i} className={cls}>
                  <span className="at">{t.at}</span>
                  <span>
                    <span className="what">{t.message}</span>
                    {i === nextIdx && <span className="in">peste {duration((minutesOf(t.at) - nowMin) * 60000)}</span>}
                  </span>
                </li>
              );
            })}
          </ol>
        )}
        <div className="sched-foot"><button className="link" onClick={onNewTask}>Task nou</button></div>
      </div>
    </section>
  );
}
