// kanopy - Developed by acidvegas in TypeScript (https://github.com/acidvegas)
// agents/opencode.ts

// Adds the kanopy project and open tasks for the session's directory to the system prompt, fetched once per session.
import { Plugin } from "@opencode/plugin"
import { readFileSync } from "node:fs"
import { homedir } from "node:os"

const BASE  = process.env.KANOPY_URL ?? "http://127.0.0.1:8000"
const TOKEN = `${homedir()}/.config/opencode/secrets/kanopy.token`
const UA    = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"

export default Plugin.define({
  id: "kanopy",
  setup: async (ctx) => {
    const briefs = new Map<string, Promise<string>>()

    const brief = (sessionID: string) => {
      if (!briefs.has(sessionID))
        briefs.set(sessionID, (async () => {
          try {
            const cwd = ((await ctx.session.get({ sessionID })) as { location?: { directory?: string } })?.location?.directory
            if (!cwd) return ""
            const resp = await fetch(`${BASE}/api/context?cwd=${encodeURIComponent(cwd)}`, { headers: { "X-Key": readFileSync(TOKEN, "utf8").trim(), "User-Agent": UA }, signal: AbortSignal.timeout(3_000) })
            return resp.ok ? (await resp.text()).trim() : ""
          } catch {
            return ""
          }
        })())
      return briefs.get(sessionID)!
    }

    const add = async (event: { sessionID: string; system: { type: string; text: string }[] }) => {
      const text = await brief(event.sessionID)
      if (text) event.system.push({ type: "text", text })
    }

    await ctx.session.hook("context", add)
  },
})
