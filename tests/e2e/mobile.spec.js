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
      estimated_bytes: 12 * 1024 * 1024,
      token: "mock-token",
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
  await page.addInitScript(() => {
    Object.defineProperty(window, "showSaveFilePicker", { value: undefined, configurable: true });
    Object.defineProperty(navigator, "share", { value: undefined, configurable: true });
    Object.defineProperty(navigator, "canShare", { value: undefined, configurable: true });
  });
  await page.goto("/");
});

test("halaman tetap utuh pada viewport ponsel", async ({ page }) => {
  await expect(page.getByRole("heading", { name: "Pilih kualitasnya. Simpan medianya." })).toBeVisible();
  await expect(page.getByLabel("Tempel link media")).toBeVisible();

  const viewportFits = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
  expect(viewportFits).toBe(true);

  for (const button of await page.getByRole("button").all()) {
    if (await button.isVisible()) {
      const box = await button.boundingBox();
      expect(box.height).toBeGreaterThanOrEqual(44);
    }
  }
});

test("alur analisis berakhir pada tombol simpan yang dapat mengunduh file", async ({ page }) => {
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

  await page.getByLabel("Tempel link media").fill("https://example.com/video");
  await page.getByRole("button", { name: "Lihat pilihan" }).click();
  await expect(page.getByText("Contoh media untuk pengujian")).toBeVisible();

  await page.getByRole("link", { name: /720p/ }).click();

  await expect(page.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  await expect(page.getByText("File siap disimpan", { exact: true })).toBeVisible();

  const trackBox = await page.locator(".progress-track").boundingBox();
  const saveBox = await page.getByRole("button", { name: "Simpan ke perangkat", exact: true }).boundingBox();
  expect(saveBox.y).toBeGreaterThan(trackBox.y + trackBox.height + 16);

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Simpan ke perangkat", exact: true }).click();
  const download = await downloadPromise;

  expect(download.suggestedFilename()).toBe("contoh.mp4");
});

test("menu beberapa link hanya mengunduh media yang dipilih", async ({ page }) => {
  await page.route("**/api/info", async route => {
    const url = route.request().postDataJSON().url;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ...mediaPayload, title: url.endsWith("/two") ? "Media kedua" : "Media pertama" })
    });
  });

  const archiveBytes = Buffer.from("zip-bytes", "utf8");
  const responseBody = Buffer.concat([
    frame("P", { percent: 50, phase: "Mengunduh media · 1/1", detail: "1 MB/s" }),
    frame("M", { filename: "linkdrop-part-1.zip", content_type: "application/zip", size: archiveBytes.length }),
    frame("D", archiveBytes),
    frame("C", { ok: true })
  ]);
  await page.route("**/api/batch/download?progress=1", async route => {
    expect(route.request().postDataJSON().tokens).toEqual(["mock-token"]);
    await route.fulfill({
      status: 200,
      contentType: "application/vnd.linkdrop.progress",
      body: responseBody
    });
  });

  await page.getByRole("tab", { name: "Beberapa link" }).click();
  await expect(page.getByLabel("Tempel beberapa link")).toBeVisible();
  await expect(page.getByLabel("Tempel link media")).toBeHidden();
  await expect(page.getByRole("button", { name: "Analisis antrean berikutnya" })).toBeHidden();
  await page.getByLabel("Tempel beberapa link").fill("https://example.com/one\nhttps://example.com/two");
  await page.getByRole("button", { name: "Analisis semua" }).click();

  await expect(page.locator(".batch-item")).toHaveCount(2);
  await expect(page.getByText("2 dari 2 media dipilih")).toBeVisible();
  await page.locator(".batch-check").nth(1).uncheck();
  await expect(page.getByText("1 dari 2 media dipilih")).toBeVisible();
  await page.getByRole("button", { name: "Unduh sebagai ZIP" }).click();

  await expect(page.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  await expect(page.getByText("File siap disimpan", { exact: true })).toBeVisible();
});

test("antrean di atas sepuluh media menampilkan peringatan", async ({ page }) => {
  await page.route("**/api/info", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(mediaPayload)
  }));
  const urls = Array.from({ length: 11 }, (_, index) => `https://example.com/media-${index + 1}`).join("\n");

  await page.getByRole("tab", { name: "Beberapa link" }).click();
  await page.getByLabel("Tempel beberapa link").fill(urls);
  await page.getByRole("button", { name: "Analisis semua" }).click();

  await expect(page.locator(".batch-item")).toHaveCount(11);
  await expect(page.getByText("11 media akan diproses secara bertahap.", { exact: false })).toBeVisible();
});
