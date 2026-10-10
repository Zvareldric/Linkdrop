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

const richMediaPayload = {
  ...mediaPayload,
  choices: {
    video: mediaPayload.choices.video,
    audio: [{
      id: "audio:128",
      label: "MP3 128 kbps",
      detail: "MP3 · 128 kbps",
      estimated_bytes: 2 * 1024 * 1024,
      token: "audio-token",
      download_url: "/api/download/audio"
    }],
    photo: [{
      id: "photo:original",
      label: "Media asli",
      detail: "JPG · file asli",
      estimated_bytes: 1 * 1024 * 1024,
      token: "photo-token",
      download_url: "/api/download/photo"
    }]
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
    Object.defineProperty(navigator, "clipboard", {
      value: { readText: async () => window.__linkdropClipboard || "" },
      configurable: true
    });
  });
  await page.goto("/");
});

test("halaman tetap utuh pada viewport ponsel", async ({ page }) => {
  await expect(page.getByRole("heading", { name: "Pilih kualitasnya. Simpan medianya." })).toBeVisible();
  await expect(page.getByLabel("Tempel link media")).toBeVisible();
  await expect(page.locator(".brand-symbol")).toHaveAttribute("src", "/static/mark.svg");
  await expect(page.locator(".brand-wordmark")).toHaveAttribute("src", "/static/wordmark.svg");
  await expect(page.locator(".transfer-visual")).toBeVisible();
  await expect(page.locator(".media-slip")).toHaveCount(3);
  await expect(page.locator(".media-icon")).toHaveCount(3);
  await expect(page.locator(".media-line")).toHaveCount(3);
  await expect(page.locator(".platform-marquee")).toBeVisible();
  await expect(page.locator(".platform-logo-set").first().locator(".platform-logo")).toHaveCount(14);
  await expect(page.locator(".platform-logo-set").first().locator("img").first()).toHaveAttribute("src", "/static/platforms/youtube.svg");
  await expect(page.locator(".input-shell button, .batch-input-shell button")).toHaveCount(0);
  await expect(page.locator(".input-actions button")).toHaveCount(2);
  await expect(page.locator(".batch-input-actions button")).toHaveCount(2);

  const viewportFits = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
  expect(viewportFits).toBe(true);

  if (await page.evaluate(() => window.innerWidth > 720)) {
    await expect(page.locator(".hero")).toHaveCSS("display", "grid");
  }

  for (const button of await page.getByRole("button").all()) {
    if (await button.isVisible()) {
      const box = await button.boundingBox();
      expect(box.height).toBeGreaterThanOrEqual(44);
    }
  }
});

test("pilihan bahasa menerjemahkan antarmuka tanpa mengubah layout", async ({ page }) => {
  await expect(page.getByText(/^Maks file: \d+ MB$/)).toHaveCount(2);
  await page.getByRole("button", { name: "EN", exact: true }).click();

  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByRole("heading", { name: "Choose the quality. Save the media." })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Multiple links" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Multiple links" })).toHaveCSS("font-size", "15px");
  await expect(page.getByText(/^Max file: \d+ MB$/)).toHaveCount(2);
  await expect(page.getByRole("button", { name: "EN", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#saveDownload")).toHaveText("Save to device");
  await expect(page.locator("#saveDownload")).toHaveCSS("font-size", "14px");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

  await page.getByRole("button", { name: "ID", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "id");
  await expect(page.locator("#saveDownload")).toHaveText("Simpan ke perangkat");
  await expect(page.getByRole("heading", { name: "Pilih kualitasnya. Simpan medianya." })).toBeVisible();
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
  await expect(page.getByRole("progressbar").getByText("File siap disimpan", { exact: true })).toBeVisible();
  const saveDialog = page.getByRole("dialog", { name: "File siap disimpan" });
  await expect(saveDialog).toBeVisible();
  const filenameInput = saveDialog.getByLabel("Nama file");
  await expect(filenameInput).toHaveValue("contoh.mp4");

  const saveBox = await saveDialog.getByRole("button", { name: "Simpan sekarang", exact: true }).boundingBox();
  expect(saveBox.height).toBeGreaterThanOrEqual(44);
  await saveDialog.getByRole("button", { name: "Nanti saja", exact: true }).click();
  await expect(saveDialog).toBeHidden();
  await page.getByRole("button", { name: "Simpan ke perangkat", exact: true }).click();
  await expect(saveDialog).toBeVisible();
  await saveDialog.press("Escape");
  await expect(saveDialog).toBeHidden();
  await page.getByRole("button", { name: "Simpan ke perangkat", exact: true }).click();
  await filenameInput.fill("hasil-pengujian");

  const downloadPromise = page.waitForEvent("download");
  await saveDialog.getByRole("button", { name: "Simpan sekarang", exact: true }).click();
  const download = await downloadPromise;

  expect(download.suggestedFilename()).toBe("hasil-pengujian.mp4");
});

test("kontrol mode, clipboard, format, dan pilihan batch memperbarui antarmuka", async ({ page }) => {
  await page.route("**/api/info", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(richMediaPayload)
  }));

  await page.evaluate(() => { window.__linkdropClipboard = "https://example.com/single"; });
  await page.locator("#pasteButton").click();
  expect(await page.getByLabel("Tempel link media").evaluate(element => element.value)).toBe("https://example.com/single");
  await page.getByRole("button", { name: "Lihat pilihan" }).click();

  await page.getByRole("button", { name: "Audio" }).click();
  await expect(page.getByRole("link", { name: /MP3 128 kbps/ })).toBeVisible();
  await page.getByRole("button", { name: "Foto" }).click();
  await expect(page.getByRole("link", { name: /Media asli/ })).toBeVisible();

  await page.getByRole("tab", { name: "Beberapa link" }).click();
  await page.evaluate(() => { window.__linkdropClipboard = "https://example.com/one\nhttps://example.com/two"; });
  await page.locator("#batchPasteButton").click();
  expect(await page.getByLabel("Tempel beberapa link").evaluate(element => element.value)).toContain("https://example.com/two");
  await page.getByRole("button", { name: "Analisis", exact: true }).click();

  await expect(page.locator(".batch-item")).toHaveCount(2);
  await page.locator(".batch-choice").first().selectOption({ index: 1 });
  await page.getByRole("button", { name: "Batalkan semua pilihan" }).click();
  await expect(page.getByText("0 dari 2 media dipilih")).toBeVisible();
  await page.getByRole("button", { name: "Pilih semua" }).click();
  await expect(page.getByText("2 dari 2 media dipilih")).toBeVisible();

  await page.getByRole("tab", { name: "Satu link" }).click();
  await expect(page.getByLabel("Tempel link media")).toBeVisible();
});

test("pembagian paket menjelaskan batas penyimpanan kepada pengguna", async ({ page }) => {
  const largeMediaPayload = {
    ...mediaPayload,
    choices: {
      video: [{
        ...mediaPayload.choices.video[0],
        estimated_bytes: 1024 * 1024 * 1024
      }],
      audio: [],
      photo: []
    }
  };
  await page.route("**/api/info", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(largeMediaPayload)
  }));

  await page.getByRole("tab", { name: "Beberapa link" }).click();
  await page.getByLabel("Tempel beberapa link").fill("https://example.com/one\nhttps://example.com/two");
  await page.getByRole("button", { name: "Analisis", exact: true }).click();

  await expect(page.getByRole("status")).toContainText("Unduhan dibagi menjadi 2 paket ZIP");
  await expect(page.getByRole("status")).toContainText("agar tiap paket tetap aman");
  await expect(page.getByRole("button", { name: "Unduh paket 1" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Unduh paket 2" })).toBeVisible();
});

test("tombol batalkan menghentikan unduhan yang masih berjalan", async ({ page }) => {
  await page.route("**/api/info", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(mediaPayload)
  }));
  await page.evaluate(() => {
    const originalFetch = window.fetch.bind(window);
    window.fetch = (url, options = {}) => {
      if (!String(url).includes("/api/download/mock")) return originalFetch(url, options);
      return new Promise((resolve, reject) => {
        options.signal?.addEventListener("abort", () => {
          reject(new DOMException("Aborted", "AbortError"));
        });
      });
    };
  });

  await page.getByLabel("Tempel link media").fill("https://example.com/video");
  await page.getByRole("button", { name: "Lihat pilihan" }).click();
  await page.getByRole("link", { name: /720p/ }).click();
  await expect(page.getByRole("button", { name: "Batalkan" })).toBeVisible();
  await page.getByRole("button", { name: "Batalkan" }).click();
  await expect(page.getByRole("progressbar").getByText("Unduhan gagal", { exact: true })).toBeVisible();
  await expect(page.getByText("Unduhan dibatalkan.", { exact: true })).toBeVisible();
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
  await page.getByRole("button", { name: "Analisis", exact: true }).click();

  await expect(page.locator(".batch-item")).toHaveCount(2);
  const choiceAppearance = await page.locator(".batch-choice").first().evaluate(element => ({
    backgroundImage: getComputedStyle(element).backgroundImage
  }));
  expect(choiceAppearance.backgroundImage).not.toBe("none");
  await expect(page.getByText("2 dari 2 media dipilih")).toBeVisible();
  await page.locator(".batch-check").nth(1).uncheck();
  await expect(page.getByText("1 dari 2 media dipilih")).toBeVisible();
  await page.getByRole("button", { name: "Unduh sebagai ZIP" }).click();

  await expect(page.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  await expect(page.getByRole("dialog", { name: "File siap disimpan" })).toBeVisible();
});

test("antrean panjang menampilkan peringatan dan tombol antrean berikutnya berfungsi", async ({ page }) => {
  await page.route("**/api/info", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(mediaPayload)
  }));
  const urls = Array.from({ length: 31 }, (_, index) => `https://example.com/media-${index + 1}`).join("\n");

  await page.getByRole("tab", { name: "Beberapa link" }).click();
  await page.getByLabel("Tempel beberapa link").fill(urls);
  await page.getByRole("button", { name: "Analisis", exact: true }).click();

  await expect(page.locator(".batch-item")).toHaveCount(30);
  await expect(page.getByText("30 media akan diproses secara bertahap.", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Analisis 1 link berikutnya" }).click();
  await expect(page.locator(".batch-item")).toHaveCount(31);
});
