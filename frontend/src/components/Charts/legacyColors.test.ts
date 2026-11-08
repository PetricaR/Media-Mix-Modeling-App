import { describe, expect, it } from "vitest";
import { rethemeFigure, swapLegacyColour } from "./legacyColors";
import { channelColor } from "../../theme";

describe("swapLegacyColour", () => {
  it("maps old channel colours to the channel's current colour", () => {
    expect(swapLegacyColour("#0A63BD")).toBe(channelColor("SEA"));
    expect(swapLegacyColour("#8c723b")).toBe(channelColor("Catalog"));
    expect(swapLegacyColour("#BE2D2F")).toBe(channelColor("Affiliate"));
    expect(swapLegacyColour("#E39214")).toBe(channelColor("Social Media"));
    expect(swapLegacyColour("#7A5195")).toBe(channelColor("PSM"));
  });

  it("keeps the alpha of rgba colours and leaves unknown values alone", () => {
    expect(swapLegacyColour("rgba(10,99,189,0.16)")).toBe("rgba(25, 87, 210, 0.16)");
    expect(swapLegacyColour("rgba(0,66,102,0.14)")).toBe("rgba(27, 49, 91, 0.14)");
    expect(swapLegacyColour("rgba(140, 114, 59, 0.10)")).toBe("rgba(25, 87, 210, 0.07)");
    expect(swapLegacyColour("#123456")).toBe("#123456");
    expect(swapLegacyColour("Spend #0A63BD")).toBe("Spend #0A63BD");
    expect(swapLegacyColour("2024-01-01")).toBe("2024-01-01");
  });
});

describe("rethemeFigure", () => {
  it("swaps colours deep inside traces and colorscales without touching the input", () => {
    const data = [
      { name: "SEA", marker: { color: ["#0A63BD", "#8C723B"] }, y: [1, 2], colorscale: [[0, "#F9F6F1"], [1, "#776132"]] },
    ];
    const out = rethemeFigure(data);
    expect(out[0].marker.color).toEqual([channelColor("SEA"), channelColor("Catalog")]);
    expect(out[0].colorscale).toEqual([[0, "#F8FAFD"], [1, "#08348A"]]);
    expect(out[0].y).toEqual([1, 2]);
    expect(out[0].name).toBe("SEA");
    expect(data[0].marker.color[0]).toBe("#0A63BD");
  });

  it("assigns distinct colours to common MMM channels and unknown custom channels", () => {
    const channels = ["Instagram", "Google ADS", "OTT", "TV", "Influencer", "Youtube", "Newspaper"];
    const colours = channels.map((c) => channelColor(c));
    expect(new Set(colours).size).toBe(channels.length);

    const custom = ["Custom Alpha", "Custom Beta", "Custom Gamma"];
    const customColours = custom.map((c) => channelColor(c));
    expect(new Set(customColours).size).toBe(custom.length);
    // Repeated lookups for the same unknown channel are stable
    expect(channelColor("Custom Alpha")).toBe(customColours[0]);
  });
});
