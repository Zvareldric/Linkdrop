const { test, expect } = require("@playwright/test");

const mediaPayload = {
  title: "Contoh media untuk pengujian",
  uploader: "Linkdrop QA",
  duration: 125,
  thumbnail: null,
  platform: "YouTube",
  item_count: 1,
  choices: {
    video: [{
      id: "video:720",
      label: "720p",
      detail: "MP4 · hingga 720p · ~12 MB",
      download_url: "/api/download/mock"
    }],
    audio: [],
    photo: []
  }
};

function frame(type, payload = Buffer.alloc(0)) {
  const body = Buffer.isBuffer(payload)
    ? payload
    : Buffer.from(JSON.stringify(payload), "utf8");
  const header = Buffer.alloc(5);
  header.write(type, 0, 1, "ascii");
  header.writeUInt32BE(body.length, 1);
  return Buffer.concat([header, body]);
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
});

test("halaman tetap utuh pada viewport ponsel", async ({ page }) => {
  await expect(page.getByRole("heading", { name: "Pindahkan media tanpa menebak kualitas." })).toBeVisible();
  await expect(page.getByLabel("Link media publik")).toBeVisible();

  const viewportFits = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
  expect(viewportFits).toBe(true);

  for (const button of await page.getByRole("button").all()) {
    if (await button.isVisible()) {
      const box = await button.boundingBox();
      expect(box.height).toBeGreaterThanOrEqual(44);
    }
  }
});

test("alur analisis dan progres unduhan berakhir pada 100 persen", async ({ page }) => {
  await page.route("**/api/info", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(mediaPayload)
  }));

  const fileBytes = Buffer.from("media-bytes", "utf8");
  const responseBody = Buffer.concat([
    frame("P", { percent: 48, phase: "Mengunduh media", detail: "2.4 MB/s" }),
    frame("M", { filename: "contoh.mp4", content_type: "video/mp4", size: fileBytes.length }),
    frame("D", fileBytes),
    frame("C", { ok: true })
  ]);
  await page.route("**/api/download/mock?progress=1", route => route.fulfill({
    status: 200,
    contentType: "application/vnd.linkdrop.progress",
    body: responseBody
  }));

  await page.getByLabel("Link media publik").fill("https://example.com/video");
  await page.getByRole("button", { name: "Analisis link" }).click();
  await expect(page.getByText("Contoh media untuk pengujian")).toBeVisible();

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("link", { name: /720p/ }).click();
  const download = await downloadPromise;

  await expect(page.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  await expect(page.getByText("Unduhan selesai", { exact: true })).toBeVisible();
  expect(download.suggestedFilename()).toBe("contoh.mp4");
});
