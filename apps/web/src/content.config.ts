import { defineCollection, z } from "astro:content";

const about = defineCollection({
  type: "content",
  schema: z.object({
    name: z.string(),
    role: z.string(),
    tagline: z.string(),
    avatar: z.string().optional(),
    skills: z.array(z.string()).default([]),
  }),
});

const projects = defineCollection({
  type: "content",
  schema: z.object({
    title: z.string(),
    date: z.coerce.date(),
    tags: z.array(z.string()).default([]),
    featured: z.boolean().default(false),
    url: z.string().optional(),
    sourceUrl: z.string().optional(),
    status: z.enum(["done", "wip", "archived"]).default("done"),
  }),
});

const resume = defineCollection({
  type: "content",
  schema: z.object({
    name: z.string(),
    role: z.string(),
    location: z.string().optional(),
    email: z.string().optional(),
    website: z.string().optional(),
    summary: z.string(),
  }),
});

export const collections = { about, projects, resume };