import {WorkflowEntrypoint} from 'cloudflare:workers';
import {NonRetryableError} from 'cloudflare:workflows';
import {handle} from './router.js';
import {runJob, tick} from './jobs.js';

export class ReportJob extends WorkflowEntrypoint {
  async run(event, step) {
    const guarded = {
      sleep: (...args) => step.sleep(...args),
      do: (name, config, callback) => step.do(name, config, async () => {
        try { return await callback(); }
        catch (error) {
          if (error.status >= 400 && error.status < 500 && error.status !== 429) throw new NonRetryableError(error.message);
          throw error;
        }
      })
    };
    return runJob(this.env, event.payload.jobId, guarded);
  }
}
export default {
  fetch: handle,
  async scheduled(controller, env, ctx) { ctx.waitUntil(tick(env, new Date(controller.scheduledTime))); }
};
