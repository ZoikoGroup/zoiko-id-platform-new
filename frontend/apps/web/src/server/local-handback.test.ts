/** Local sign-in return: from the registered 127.0.0.1 address back to the app's own origin, and nowhere else. */
import { describe, expect, it } from "vitest";

import { localHandback } from "./local-handback";

const HOME = "http://localhost:3000";

describe("localHandback", () => {
  it("sends a callback that came back on 127.0.0.1 on to the app's own origin, keeping path and query", () => {
    expect(localHandback("http://127.0.0.1:3000/auth/callback?code=abc&state=xyz", HOME, "local")).toBe(
      "http://localhost:3000/auth/callback?code=abc&state=xyz",
    );
  });

  it("goes by the host the browser asked for when the dev server describes the URL with its own name", () => {
    expect(localHandback("http://localhost:3000/auth/callback?code=a&state=b", HOME, "local", "127.0.0.1:3000")).toBe("http://localhost:3000/auth/callback?code=a&state=b");
    expect(localHandback("http://localhost:3000/auth/callback?code=a&state=b", HOME, "local", "localhost:3000")).toBeNull();
  });

  it("does nothing on the app's own host (so it cannot loop)", () => {
    expect(localHandback("http://localhost:3000/auth/callback?code=abc&state=xyz", HOME, "local")).toBeNull();
  });

  it("does nothing outside the local environment, whatever the host", () => {
    for (const env of ["production", "sandbox", "test", "development"]) {
      expect(localHandback("http://127.0.0.1:3000/auth/callback?code=a&state=b", HOME, env)).toBeNull();
    }
  });

  it("only ever hands back from a loopback IP, never from another host", () => {
    for (const host of ["https://evil.example.com", "http://192.168.0.5:3000", "http://localhost.evil.example:3000", "http://10.0.0.1:3000"]) {
      expect(localHandback(`${host}/auth/callback?code=a&state=b`, HOME, "local")).toBeNull();
    }
    for (const header of ["evil.example.com", "192.168.0.5:3000", "127.0.0.1.evil.example:3000"]) {
      expect(localHandback("http://localhost:3000/auth/callback?code=a&state=b", HOME, "local", header)).toBeNull();
    }
  });
});
