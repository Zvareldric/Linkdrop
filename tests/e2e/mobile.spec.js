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

function readyJob(filename, size = 11) {
  return { state: "ready", percent: 100, phase: "Selesai", detail: "", filename, size, file_url: "/api/jobs/job-1/file" };
}

// Mocks the background job API: start → status polling → file download.
// A status of "offline" makes that poll fail like a phone without network.
async function mockJob(page, { start = "**/api/download/mock**", statuses, onStart, onCancel }) {
  const queue = [...statuses];
  await page.route(start, async route => {
    if (route.request().method() !== "POST") return route.fallback();
    onStart?.(route.request());
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ job: "job-1", state: "running", percent: 0, phase: "Memulai unduhan", detail: "" })
    });
  });
  await page.route("**/api/jobs/job-1", async route => {
    if (route.request().method() === "DELETE") {
      onCancel?.();
      return route.fulfill({ status: 204, body: "" });
    }
    const next = queue.length > 1 ? queue.shift() : queue[0];
    if (next === "offline") return route.abort("internetdisconnected");
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(next) });
  });
  await page.route("**/api/jobs/job-1/file**", route => {
    const name = new URL(route.request().url()).searchParams.get("name") || "download";
    return route.fulfill({
      status: 200,
      headers: {
        "Content-Type": "application/octet-stream",
        "Content-Disposition": `attachment; filename*=UTF-8''${encodeURIComponent(name)}`
      },
      body: "media-bytes"
    });
  });
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

test("browser tidak menyimpan riwayat link yang pernah ditempel", async ({ page }) => {
  await expect(page.locator("#analyzeForm")).toHaveAttribute("autocomplete", "off");
  await expect(page.getByLabel("Tempel link media")).toHaveAttribute("autocomplete", "off");
  await expect(page.locator("#batchForm")).toHaveAttribute("autocomplete", "off");
  await expect(page.locator("#batchInput")).toHaveAttribute("autocomplete", "off");
});

test("dekorasi latar tetap terlihat proporsional pada layar ultrawide", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-chrome", "Viewport ultrawide hanya diuji di desktop.");
  await page.setViewportSize({ width: 2560, height: 1440 });

  const visibleWidth = await page.evaluate(() => {
    const before = getComputedStyle(document.body, "::before");
    const after = getComputedStyle(document.body, "::after");
    return {
      circle: Number.parseFloat(before.width) + Number.parseFloat(before.left),
      star: Number.parseFloat(after.width) + Number.parseFloat(after.right),
    };
  });

  expect(visibleWidth.circle).toBeGreaterThan(80);
  expect(visibleWidth.star).toBeGreaterThan(80);
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

const IPHONE_USER_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1";

async function openSingleResult(page, payload = mediaPayload) {
  await page.route("**/api/info", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(payload)
  }));
  await page.getByLabel("Tempel link media").fill("https://example.com/video");
  await page.getByRole("button", { name: "Lihat pilihan" }).click();
  await expect(page.getByText(payload.title)).toBeVisible();
}

test.describe("simpan otomatis saat file siap", () => {
  // iPhone Safari only allows downloads started by a tap; see the next group.
  test.skip(({ browserName }) => browserName === "webkit", "iPhone Safari memakai tombol simpan");

  test("file langsung disimpan dan tombol simpan tetap tersedia sebagai cadangan", async ({ page }) => {
    await openSingleResult(page);
    await mockJob(page, {
      statuses: [{ state: "running", percent: 48, phase: "Mengunduh media", detail: "2.4 MB/s" }, readyJob("contoh.mp4")]
    });

    const autoDownload = page.waitForEvent("download");
    await page.getByRole("link", { name: /720p/ }).click();
    await expect(page.getByText("Server sedang menyiapkan file", { exact: false })).toBeVisible();
    expect((await autoDownload).suggestedFilename()).toBe("contoh.mp4");

    await expect(page.getByRole("progressbar").getByText("Menyimpan ke perangkat", { exact: true })).toBeVisible();
    await expect(page.getByText("Browser sedang memindahkan contoh.mp4 ke perangkat ini", { exact: false })).toBeVisible();
    await expect(page.getByText("Jika unduhan tidak muncul", { exact: false })).toBeVisible();
    await expect(page.getByRole("dialog", { name: "File siap disimpan" })).toBeHidden();
    expect(await page.evaluate(() => localStorage.getItem("linkdrop-active-job"))).toBeNull();

    await page.getByRole("button", { name: "Simpan ke perangkat", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "File siap disimpan" });
    await expect(dialog.getByText("memindahkan file dari server ke perangkat ini", { exact: false })).toBeVisible();
    await dialog.getByLabel("Nama file").fill("salinan");
    const manualDownload = page.waitForEvent("download");
    await dialog.getByRole("button", { name: "Simpan sekarang", exact: true }).click();
    expect((await manualDownload).suggestedFilename()).toBe("salinan.mp4");
  });

  test("file yang selesai saat halaman tersembunyi disimpan ketika halaman dibuka lagi", async ({ page }) => {
    await page.evaluate(() => {
      let state = "hidden";
      Object.defineProperty(document, "visibilityState", { get: () => state, configurable: true });
      window.__showPage = () => {
        state = "visible";
        document.dispatchEvent(new Event("visibilitychange"));
      };
    });
    let downloads = 0;
    page.on("download", () => { downloads += 1; });
    await openSingleResult(page);
    await mockJob(page, { statuses: [readyJob("contoh.mp4")] });

    await page.getByRole("link", { name: /720p/ }).click();
    await expect(page.getByRole("progressbar").getByText("File siap di server", { exact: true })).toBeVisible();
    await page.waitForTimeout(1500);
    expect(downloads).toBe(0);

    const download = page.waitForEvent("download");
    await page.evaluate(() => window.__showPage());
    expect((await download).suggestedFilename()).toBe("contoh.mp4");
  });

  test("unduhan tetap berlanjut setelah koneksi putus saat layar mati", async ({ page }) => {
    await openSingleResult(page);
    await mockJob(page, {
      statuses: [
        { state: "running", percent: 30, phase: "Mengunduh media", detail: "1 MB/s" },
        "offline",
        "offline",
        readyJob("contoh.mp4")
      ]
    });

    const download = page.waitForEvent("download", { timeout: 20000 });
    await page.getByRole("link", { name: /720p/ }).click();
    await expect(page.getByText("Menunggu koneksi ke server", { exact: false })).toBeVisible();
    expect((await download).suggestedFilename()).toBe("contoh.mp4");
    await expect(page.getByText("Unduhan gagal", { exact: true })).toBeHidden();
  });
});

test.describe("iPhone menyimpan file lewat tombol", () => {
  test.use({ userAgent: IPHONE_USER_AGENT });

  test("alur analisis berakhir pada tombol simpan yang dapat mengunduh file", async ({ page }) => {
    await openSingleResult(page);
    await mockJob(page, {
      statuses: [{ state: "running", percent: 48, phase: "Mengunduh media", detail: "2.4 MB/s" }, readyJob("contoh.mp4")]
    });

    await page.getByRole("link", { name: /720p/ }).click();
    await expect(page.getByText("Server sedang menyiapkan file", { exact: false })).toBeVisible();

    await expect(page.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
    await expect(page.getByRole("progressbar").getByText("File siap di server", { exact: true })).toBeVisible();
    const saveDialog = page.getByRole("dialog", { name: "File siap disimpan" });
    await expect(saveDialog).toBeVisible();
    await expect(saveDialog.getByText("memindahkan file dari server ke perangkat ini", { exact: false })).toBeVisible();
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
    await expect(page.getByRole("progressbar").getByText("Menyimpan ke perangkat", { exact: true })).toBeVisible();
    await expect(page.getByText("Browser sedang memindahkan hasil-pengujian.mp4", { exact: false })).toBeVisible();
    await expect(page.getByText("Jika unduhan tidak muncul", { exact: false })).toBeHidden();
  });

  test("proses yang berjalan dilanjutkan setelah halaman dimuat ulang", async ({ page }) => {
    await mockJob(page, {
      statuses: [{ state: "running", percent: 64, phase: "Mengonversi video", detail: "64%" }, readyJob("lanjutan.mp4")]
    });
    await page.evaluate(() => localStorage.setItem("linkdrop-active-job", JSON.stringify({ id: "job-1", label: "720p" })));
    await page.reload();

    await expect(page.getByRole("progressbar").getByText("Mengonversi video", { exact: true })).toBeVisible();
    const dialog = page.getByRole("dialog", { name: "File siap disimpan" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel("Nama file")).toHaveValue("lanjutan.mp4");

    const downloadPromise = page.waitForEvent("download");
    await dialog.getByRole("button", { name: "Simpan sekarang", exact: true }).click();
    expect((await downloadPromise).suggestedFilename()).toBe("lanjutan.mp4");
    expect(await page.evaluate(() => localStorage.getItem("linkdrop-active-job"))).toBeNull();
  });

  test("dialog simpan dan informasi batas tetap muat untuk nama file yang sangat panjang", async ({ page }) => {
    const longName = `${"file_media_sangat_panjang_".repeat(12)}.mp4`;
    await openSingleResult(page, { ...mediaPayload, title: "judul_media_sangat_panjang_tanpa_spasi_untuk_menguji_lebar_layar_mobile" });
    await mockJob(page, { statuses: [readyJob(longName, 1)] });

    await page.getByRole("link", { name: /720p/ }).click();

    const dialog = page.getByRole("dialog", { name: "File siap disimpan" });
    await expect(dialog).toBeVisible();
    const fitsViewport = await dialog.evaluate(element => {
      const box = element.getBoundingClientRect();
      return box.left >= 0 && box.right <= window.innerWidth;
    });
    expect(fitsViewport).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

    const maxFile = page.locator("#singlePanel .helper > span:last-child");
    const maxFileFits = await maxFile.evaluate(element => element.getBoundingClientRect().right <= window.innerWidth);
    expect(maxFileFits).toBe(true);
  });
});

test("proses yang sudah kedaluwarsa tidak dimunculkan lagi", async ({ page }) => {
  await page.route("**/api/jobs/old-job", route => route.fulfill({
    status: 404,
    contentType: "application/json",
    body: JSON.stringify({ error: "Proses unduhan tidak ditemukan." })
  }));
  await page.evaluate(() => localStorage.setItem("linkdrop-active-job", JSON.stringify({ id: "old-job", label: "720p" })));
  await page.reload();

  await expect.poll(() => page.evaluate(() => localStorage.getItem("linkdrop-active-job"))).toBeNull();
  await expect(page.getByRole("progressbar")).toBeHidden();
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
  let cancelled = false;
  await mockJob(page, {
    statuses: [{ state: "running", percent: 20, phase: "Mengunduh media", detail: "1 MB/s" }],
    onCancel: () => { cancelled = true; }
  });

  await page.getByLabel("Tempel link media").fill("https://example.com/video");
  await page.getByRole("button", { name: "Lihat pilihan" }).click();
  await page.getByRole("link", { name: /720p/ }).click();
  await expect(page.getByRole("progressbar").getByText("Mengunduh media", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Batalkan" }).click();
  await expect(page.getByRole("progressbar").getByText("Unduhan gagal", { exact: true })).toBeVisible();
  await expect(page.getByText("Unduhan dibatalkan.", { exact: true })).toBeVisible();
  await expect.poll(() => cancelled).toBe(true);
  expect(await page.evaluate(() => localStorage.getItem("linkdrop-active-job"))).toBeNull();
});

test("tombol batalkan juga bekerja sebelum server membalas", async ({ page }) => {
  await page.route("**/api/info", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(mediaPayload)
  }));
  await page.route("**/api/download/mock**", () => {});

  await page.getByLabel("Tempel link media").fill("https://example.com/video");
  await page.getByRole("button", { name: "Lihat pilihan" }).click();
  await page.getByRole("link", { name: /720p/ }).click();
  await page.getByRole("button", { name: "Batalkan" }).click();
  await expect(page.getByText("Unduhan dibatalkan.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Lihat pilihan" })).toBeEnabled();
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

  let batchTokens = null;
  await mockJob(page, {
    start: "**/api/batch/download?job=1",
    statuses: [{ state: "running", percent: 50, phase: "Mengunduh media · 1/1", detail: "1 MB/s" }, readyJob("linkdrop-part-1.zip")],
    onStart: request => { batchTokens = request.postDataJSON().tokens; }
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
  await expect(page.getByRole("button", { name: "Paket siap" })).toBeVisible();
  expect(batchTokens).toEqual(["mock-token"]);
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


test("subtitle hanya bisa dipilih jika video memilikinya dan ikut dikirim ke unduhan", async ({ page }) => {
  const subtitlePayload = {
    ...richMediaPayload,
    subtitles: [
      { id: "subtitle:id", language: "id", automatic: true, token: "sub-id" },
      { id: "subtitle:en", language: "en", automatic: false, token: "sub-en" }
    ]
  };
  await page.route("**/api/info", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(subtitlePayload)
  }));
  let requestedUrl = null;
  await mockJob(page, {
    statuses: [{ state: "running", percent: 92, phase: "Menempelkan subtitle ke video", detail: "50%" }, readyJob("contoh.mp4")],
    onStart: request => { requestedUrl = request.url(); }
  });

  await page.getByLabel("Tempel link media").fill("https://www.youtube.com/watch?v=abc123");
  await page.getByRole("button", { name: "Lihat pilihan" }).click();

  const picker = page.getByRole("group", { name: "Subtitle" });
  await expect(picker).toBeVisible();
  await expect(picker.getByRole("button")).toHaveText(["Tanpa subtitle", "Indonesia (otomatis)", "Inggris"]);
  await expect(picker.getByRole("button", { name: "Tanpa subtitle" })).toHaveAttribute("aria-pressed", "true");

  await page.getByRole("button", { name: "Audio", exact: true }).click();
  await expect(picker).toBeHidden();
  await page.getByRole("button", { name: "Video", exact: true }).click();

  const modeGroup = page.getByRole("group", { name: "Tampilan subtitle" });
  await expect(modeGroup).toBeHidden();
  await picker.getByRole("button", { name: "Inggris" }).click();
  await expect(picker.getByRole("button", { name: "Inggris" })).toHaveAttribute("aria-pressed", "true");
  await expect(modeGroup.getByRole("button", { name: "Bisa dimatikan" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("Kualitas video tetap asli")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

  await page.getByRole("button", { name: "EN", exact: true }).click();
  await expect(picker.getByRole("button", { name: "English" })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("link", { name: /720p \+ English subtitles/ }).click();

  await expect(page.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  const params = new URL(requestedUrl).searchParams;
  expect(params.get("subtitle")).toBe("sub-en");
});

test("pemilih subtitle tersembunyi jika video tidak memiliki subtitle", async ({ page }) => {
  await page.route("**/api/info", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(mediaPayload)
  }));
  await page.getByLabel("Tempel link media").fill("https://example.com/video");
  await page.getByRole("button", { name: "Lihat pilihan" }).click();
  await expect(page.getByText("Contoh media untuk pengujian")).toBeVisible();
  await expect(page.getByRole("group", { name: "Subtitle" })).toBeHidden();
  await expect(page.getByRole("link", { name: /^720p/ })).toHaveAttribute("href", "/api/download/mock");
});

test("mode subtitle permanen dikirim sebagai pilihan terpisah", async ({ page }) => {
  await page.route("**/api/info", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      ...mediaPayload,
      subtitles: [{ id: "subtitle:id", language: "id", automatic: false, token: "sub-id" }]
    })
  }));
  await page.getByLabel("Tempel link media").fill("https://www.youtube.com/watch?v=abc123");
  await page.getByRole("button", { name: "Lihat pilihan" }).click();

  await page.getByRole("group", { name: "Subtitle" }).getByRole("button", { name: "Indonesia" }).click();
  const softLink = page.getByRole("link", { name: /720p \+ subtitle Indonesia MP4/ });
  await expect(softLink).toHaveAttribute("href", "/api/download/mock?subtitle=sub-id");

  const modeGroup = page.getByRole("group", { name: "Tampilan subtitle" });
  await modeGroup.getByRole("button", { name: "Selalu tampil" }).click();
  await expect(modeGroup.getByRole("button", { name: "Selalu tampil" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("Prosesnya lebih lama")).toBeVisible();
  const burnLink = page.getByRole("link", { name: /720p \+ subtitle Indonesia \(selalu tampil\)/ });
  await expect(burnLink).toHaveAttribute("href", "/api/download/mock?subtitle=sub-id&subtitle_mode=burn");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("mode permanen disembunyikan jika server tidak bisa membakar subtitle", async ({ page }) => {
  await page.route("**/api/info", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      ...mediaPayload,
      subtitle_burn: false,
      subtitles: [{ id: "subtitle:en", language: "en", automatic: false, token: "sub-en" }]
    })
  }));
  await page.getByLabel("Tempel link media").fill("https://www.youtube.com/watch?v=abc123");
  await page.getByRole("button", { name: "Lihat pilihan" }).click();
  await page.getByRole("group", { name: "Subtitle" }).getByRole("button", { name: "Inggris" }).click();

  const modeGroup = page.getByRole("group", { name: "Tampilan subtitle" });
  await expect(modeGroup.getByRole("button")).toHaveText(["Bisa dimatikan"]);
  await expect(page.getByText("FFmpeg-nya dibuat tanpa libass")).toBeVisible();
  await expect(page.getByRole("link", { name: /720p \+ subtitle Inggris MP4/ })).toHaveAttribute("href", "/api/download/mock?subtitle=sub-en");
});
